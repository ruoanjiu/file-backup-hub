from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.app.auth import Actor, ensure_machine_access
from server.app.config import Settings
from server.app.models import AuditLog, Backup, BackupFile
from server.app.schemas import BackupInitRequest, BackupListItem, BackupListResponse
from server.app.storage import get_storage_paths, read_json, write_json_atomic
from server.app.utils.time import utc_now_iso

SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


def validate_safe_id(value: str, field_name: str) -> None:
    if not SAFE_ID_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} contains unsafe characters",
        )


def validate_backup_id(backup_id: str) -> None:
    validate_safe_id(backup_id, "backup_id")


def _audit(
    db: Session,
    actor: Actor,
    action: str,
    target: str | None,
    detail_json: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action=action,
            target=target,
            ip_address=None,
            created_at=utc_now_iso(),
            detail_json=detail_json,
        )
    )


def init_backup(db: Session, payload: BackupInitRequest, actor: Actor, settings: Settings) -> Backup:
    ensure_machine_access(actor, payload.machine_id)
    validate_backup_id(payload.backup_id)
    validate_safe_id(payload.machine_id, "machine_id")
    validate_safe_id(payload.strategy_name, "strategy_name")

    manifest = payload.manifest
    if manifest.backup_id != payload.backup_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="manifest.backup_id must match backup_id",
        )
    if manifest.machine_id != payload.machine_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="manifest.machine_id must match machine_id",
        )
    if manifest.strategy_name != payload.strategy_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="manifest.strategy_name must match strategy_name",
        )

    existing = db.scalar(select(Backup).where(Backup.backup_id == payload.backup_id))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="backup_id already exists",
        )

    paths = get_storage_paths(
        settings.storage_root,
        settings.manifest_root,
        payload.machine_id,
        payload.strategy_name,
        payload.backup_id,
    )
    paths.storage_dir.mkdir(parents=True, exist_ok=True)
    paths.manifest_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(paths.manifest_path, payload.manifest.model_dump(mode="json"))

    backup = Backup(
        backup_id=payload.backup_id,
        machine_id=payload.machine_id,
        strategy_name=payload.strategy_name,
        status="PENDING",
        created_at=payload.created_at.isoformat(),
        uploaded_at=None,
        file_count=payload.file_count,
        total_size=payload.total_size,
        bundle_size=payload.bundle_size,
        bundle_sha256=payload.bundle_sha256,
        storage_path=str(paths.bundle_path),
        manifest_path=str(paths.manifest_path),
        error_message=None,
    )
    db.add(backup)

    for file_entry in manifest.files:
        db.add(
            BackupFile(
                backup_id=payload.backup_id,
                file_id=file_entry.file_id,
                original_path=file_entry.original_path,
                backup_path=file_entry.backup_path,
                file_name=file_entry.file_name,
                file_type=file_entry.file_type,
                size=file_entry.size,
                mtime=file_entry.mtime,
                sha256=file_entry.sha256,
                possibly_active=int(file_entry.possibly_active),
            )
        )

    _audit(db, actor, "backup.init", payload.backup_id)
    db.commit()
    db.refresh(backup)
    return backup


def get_backup_for_actor(db: Session, backup_id: str, actor: Actor) -> Backup:
    validate_backup_id(backup_id)
    backup = db.scalar(select(Backup).where(Backup.backup_id == backup_id))
    if backup is None or backup.status == "DELETED":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Backup not found")
    ensure_machine_access(actor, backup.machine_id)
    return backup


def backup_to_list_item(backup: Backup) -> BackupListItem:
    return BackupListItem(
        backup_id=backup.backup_id,
        machine_id=backup.machine_id,
        strategy_name=backup.strategy_name,
        status=backup.status,
        created_at=backup.created_at,
        uploaded_at=backup.uploaded_at,
        file_count=backup.file_count,
        total_size=backup.total_size,
        bundle_size=backup.bundle_size,
        bundle_sha256=backup.bundle_sha256,
    )


def mark_uploading(db: Session, backup: Backup) -> None:
    if backup.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Backup is not uploadable from status {backup.status}",
        )
    backup.status = "UPLOADING"
    backup.error_message = None
    db.commit()


def mark_upload_completed(
    db: Session,
    backup: Backup,
    bundle_sha256: str,
    bundle_size: int,
    actor: Actor,
) -> None:
    backup.status = "COMPLETED"
    backup.uploaded_at = utc_now_iso()
    backup.bundle_sha256 = bundle_sha256
    backup.bundle_size = bundle_size
    backup.error_message = None
    _audit(db, actor, "backup.upload.completed", backup.backup_id)
    db.commit()


def mark_upload_failed(db: Session, backup: Backup, error_message: str, actor: Actor) -> None:
    backup.status = "FAILED"
    backup.error_message = error_message
    _audit(db, actor, "backup.upload.failed", backup.backup_id)
    db.commit()


def list_backups(
    db: Session,
    actor: Actor,
    machine_id: str | None,
    strategy_name: str | None,
    limit: int,
    offset: int,
) -> BackupListResponse:
    if not actor.is_admin:
        if machine_id is not None and machine_id != actor.machine_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token is not allowed to list another machine_id",
            )
        machine_id = actor.machine_id

    query = select(Backup).where(Backup.status != "DELETED")
    count_query = select(func.count()).select_from(Backup).where(Backup.status != "DELETED")

    if machine_id:
        validate_safe_id(machine_id, "machine_id")
        query = query.where(Backup.machine_id == machine_id)
        count_query = count_query.where(Backup.machine_id == machine_id)
    if strategy_name:
        validate_safe_id(strategy_name, "strategy_name")
        query = query.where(Backup.strategy_name == strategy_name)
        count_query = count_query.where(Backup.strategy_name == strategy_name)

    total = db.scalar(count_query) or 0
    rows = db.scalars(
        query.order_by(Backup.created_at.desc(), Backup.id.desc()).limit(limit).offset(offset)
    ).all()

    return BackupListResponse(
        items=[backup_to_list_item(row) for row in rows],
        limit=limit,
        offset=offset,
        total=total,
    )


def load_manifest_for_backup(backup: Backup) -> dict:
    if backup.manifest_path is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Backup manifest path is missing",
        )
    manifest_path = backup.manifest_path
    try:
        return read_json(Path(manifest_path))
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Backup manifest file not found",
        ) from exc


def delete_backup(db: Session, backup: Backup, actor: Actor) -> None:
    if backup.status == "DELETED":
        return

    trash_base: Path | None = None
    moved_paths: list[str] = []
    for raw_path in [backup.storage_path, backup.manifest_path]:
        if not raw_path:
            continue
        source_dir = Path(raw_path).parent
        if not source_dir.exists():
            continue
        if trash_base is None:
            trash_base = source_dir.parents[2] / "trash" if len(source_dir.parents) >= 3 else source_dir.parent / "trash"
        target_dir = trash_base / backup.machine_id / backup.strategy_name / backup.backup_id / source_dir.parent.name
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.move(str(source_dir), str(target_dir))
        moved_paths.append(str(target_dir))

    backup.status = "DELETED"
    backup.error_message = None
    _audit(
        db,
        actor,
        "backup.deleted",
        backup.backup_id,
        detail_json=json.dumps({"moved_paths": moved_paths}, ensure_ascii=False),
    )
    db.commit()
