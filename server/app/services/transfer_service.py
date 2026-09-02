from __future__ import annotations

import hashlib
import json
import shutil
import re
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server.app.auth import Actor, ensure_machine_access
from server.app.config import Settings
from server.app.models import Client, Transfer
from server.app.storage import read_json, write_json_atomic
from server.app.transfer_schemas import (
    TransferInitRequest,
    TransferItem,
    TransferListResponse,
)
from server.app.utils.time import utc_now_iso


SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")
SERVER_RECEIVER_ID = "__server__"
SERVER_INBOX_DIR_NAME = "server-inbox"


def _validate_id(value: str, field_name: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} contains unsafe characters",
        )
    return value


def _validate_manifest_paths(payload: TransferInitRequest) -> None:
    if payload.file_count != payload.manifest.file_count:
        raise HTTPException(status_code=422, detail="file_count does not match manifest")
    if payload.total_size != payload.manifest.total_size:
        raise HTTPException(status_code=422, detail="total_size does not match manifest")
    if len(payload.manifest.files) != payload.file_count:
        raise HTTPException(status_code=422, detail="manifest.files length does not match file_count")
    for entry in payload.manifest.files:
        for raw_path in (entry.relative_path, entry.backup_path):
            path = PurePosixPath(raw_path)
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                raise HTTPException(status_code=422, detail=f"Unsafe transfer path: {raw_path}")


def _receiver_exists(db: Session, settings: Settings, device_id: str) -> bool:
    if device_id == SERVER_RECEIVER_ID:
        return True
    if device_id in settings.client_tokens:
        return True
    return db.scalar(
        select(Client.id).where(Client.machine_id == device_id, Client.enabled == 1)
    ) is not None


def init_transfer(
    db: Session,
    actor: Actor,
    settings: Settings,
    payload: TransferInitRequest,
) -> Transfer:
    ensure_machine_access(actor, payload.sender_device_id)
    _validate_id(payload.transfer_id, "transfer_id")
    _validate_id(payload.sender_device_id, "sender_device_id")
    _validate_id(payload.receiver_device_id, "receiver_device_id")
    if payload.sender_device_id == payload.receiver_device_id:
        raise HTTPException(status_code=422, detail="Sender and receiver must be different devices")
    if payload.manifest.transfer_id != payload.transfer_id:
        raise HTTPException(status_code=422, detail="manifest.transfer_id mismatch")
    if payload.manifest.sender_device_id != payload.sender_device_id:
        raise HTTPException(status_code=422, detail="manifest.sender_device_id mismatch")
    if payload.manifest.receiver_device_id != payload.receiver_device_id:
        raise HTTPException(status_code=422, detail="manifest.receiver_device_id mismatch")
    if not _receiver_exists(db, settings, payload.receiver_device_id):
        raise HTTPException(status_code=404, detail="Receiver device is not registered")
    _validate_manifest_paths(payload)

    transfer_dir = settings.transfer_root / payload.receiver_device_id / payload.transfer_id
    bundle_path = transfer_dir / "bundle.tar.gz"
    manifest_path = transfer_dir / "manifest.json"
    incoming_manifest = payload.manifest.model_dump(mode="json")
    existing = db.scalar(select(Transfer).where(Transfer.transfer_id == payload.transfer_id))
    if existing is not None:
        same = (
            existing.sender_device_id == payload.sender_device_id
            and existing.receiver_device_id == payload.receiver_device_id
            and existing.bundle_sha256 == payload.bundle_sha256
            and existing.file_count == payload.file_count
            and existing.total_size == payload.total_size
            and Path(existing.manifest_path).exists()
            and read_json(Path(existing.manifest_path)) == incoming_manifest
        )
        if not same:
            raise HTTPException(status_code=409, detail="transfer_id already exists with different metadata")
        if existing.status in {"AVAILABLE", "ACCEPTED", "COMPLETED"}:
            return existing
        if existing.status == "UPLOADING":
            raise HTTPException(status_code=409, detail="Transfer is currently uploading")
        existing.status = "PENDING"
        existing.error_message = None
        existing.updated_at = utc_now_iso()
        db.commit()
        db.refresh(existing)
        return existing

    transfer_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(manifest_path, incoming_manifest)
    now = datetime.now(UTC)
    transfer = Transfer(
        transfer_id=payload.transfer_id,
        sender_device_id=payload.sender_device_id,
        receiver_device_id=payload.receiver_device_id,
        status="PENDING",
        created_at=now.isoformat(),
        updated_at=now.isoformat(),
        expires_at=(now + timedelta(hours=payload.expires_in_hours)).isoformat(),
        file_count=payload.file_count,
        total_size=payload.total_size,
        bundle_size=payload.bundle_size,
        bundle_sha256=payload.bundle_sha256,
        storage_path=str(bundle_path),
        manifest_path=str(manifest_path),
        error_message=None,
    )
    db.add(transfer)
    db.commit()
    db.refresh(transfer)
    return transfer


def get_transfer_for_actor(db: Session, transfer_id: str, actor: Actor) -> Transfer:
    _validate_id(transfer_id, "transfer_id")
    transfer = db.scalar(select(Transfer).where(Transfer.transfer_id == transfer_id))
    if transfer is None:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if not actor.is_admin and actor.machine_id not in {
        transfer.sender_device_id,
        transfer.receiver_device_id,
    }:
        raise HTTPException(status_code=403, detail="Transfer does not belong to this device")
    return transfer


def transfer_to_item(transfer: Transfer) -> TransferItem:
    return TransferItem(
        transfer_id=transfer.transfer_id,
        sender_device_id=transfer.sender_device_id,
        receiver_device_id=transfer.receiver_device_id,
        status=transfer.status,
        created_at=transfer.created_at,
        updated_at=transfer.updated_at,
        expires_at=transfer.expires_at,
        file_count=transfer.file_count,
        total_size=transfer.total_size,
        bundle_size=transfer.bundle_size,
        bundle_sha256=transfer.bundle_sha256,
    )


def list_inbox(
    db: Session,
    actor: Actor,
    limit: int,
    *,
    receiver_device_id: str | None = None,
) -> TransferListResponse:
    if actor.machine_id is None and not actor.is_admin:
        raise HTTPException(status_code=403, detail="Device identity required")
    query = select(Transfer)
    count_query = select(func.count()).select_from(Transfer)
    if not actor.is_admin:
        query = query.where(Transfer.receiver_device_id == actor.machine_id)
        count_query = count_query.where(Transfer.receiver_device_id == actor.machine_id)
    elif receiver_device_id is not None:
        query = query.where(Transfer.receiver_device_id == receiver_device_id)
        count_query = count_query.where(Transfer.receiver_device_id == receiver_device_id)
    actionable = ("AVAILABLE", "ACCEPTED")
    query = query.where(Transfer.status.in_(actionable))
    count_query = count_query.where(Transfer.status.in_(actionable))
    rows = db.scalars(query.order_by(Transfer.created_at.desc()).limit(limit)).all()
    return TransferListResponse(
        items=[transfer_to_item(item) for item in rows],
        total=db.scalar(count_query) or 0,
    )


def set_transfer_status(
    db: Session,
    transfer: Transfer,
    actor: Actor,
    new_status: str,
) -> Transfer:
    if not actor.is_admin and actor.machine_id != transfer.receiver_device_id:
        raise HTTPException(status_code=403, detail="Only the receiver can update this transfer")
    allowed = {
        "ACCEPTED": {"AVAILABLE"},
        "COMPLETED": {"ACCEPTED"},
        "REJECTED": {"AVAILABLE", "ACCEPTED"},
    }
    if new_status not in allowed or transfer.status not in allowed[new_status]:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot change transfer from {transfer.status} to {new_status}",
        )
    transfer.status = new_status
    transfer.updated_at = utc_now_iso()
    db.commit()
    db.refresh(transfer)
    return transfer


def server_inbox_path(settings: Settings, transfer_id: str) -> Path:
    _validate_id(transfer_id, "transfer_id")
    return (
        settings.transfer_root / SERVER_INBOX_DIR_NAME / transfer_id
    ).resolve(strict=False)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def receive_transfer_on_server(
    db: Session,
    transfer: Transfer,
    actor: Actor,
    settings: Settings,
) -> tuple[Transfer, Path]:
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="Admin token required")
    if transfer.receiver_device_id != SERVER_RECEIVER_ID:
        raise HTTPException(status_code=422, detail="Transfer is not addressed to this Server")
    destination = server_inbox_path(settings, transfer.transfer_id)
    if transfer.status == "COMPLETED":
        if not destination.is_dir():
            raise HTTPException(status_code=404, detail="Server inbox files are missing")
        return transfer, destination
    if transfer.status == "AVAILABLE":
        transfer = set_transfer_status(db, transfer, actor, "ACCEPTED")
    if transfer.status != "ACCEPTED":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot receive Server transfer from {transfer.status}",
        )

    bundle_path = Path(transfer.storage_path)
    manifest = read_json(Path(transfer.manifest_path))
    if not bundle_path.is_file() or _sha256_file(bundle_path) != transfer.bundle_sha256:
        raise HTTPException(status_code=400, detail="Transfer bundle verification failed")
    if destination.exists():
        raise HTTPException(status_code=409, detail="Server inbox destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_destination = destination.with_name(destination.name + ".receiving")
    if temp_destination.exists():
        shutil.rmtree(temp_destination)
    temp_destination.mkdir(parents=True)
    try:
        with tarfile.open(bundle_path, "r:gz") as archive:
            for entry in manifest.get("files", []):
                relative = PurePosixPath(str(entry["relative_path"]))
                backup_path = PurePosixPath(str(entry["backup_path"]))
                if relative.is_absolute() or backup_path.is_absolute():
                    raise ValueError("Transfer manifest contains an absolute path")
                if any(
                    part in {"", ".", ".."}
                    for part in (*relative.parts, *backup_path.parts)
                ):
                    raise ValueError("Transfer manifest contains an unsafe path")
                member = archive.getmember(backup_path.as_posix())
                if not member.isfile():
                    raise ValueError(f"Transfer member is not a regular file: {backup_path}")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"Transfer member is missing: {backup_path}")
                target = temp_destination / Path(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("wb") as destination_file:
                    shutil.copyfileobj(source, destination_file)
                if _sha256_file(target) != entry["sha256"]:
                    raise ValueError(f"Transferred file verification failed: {relative}")
        (temp_destination / "transfer-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp_destination.replace(destination)
        transfer = set_transfer_status(db, transfer, actor, "COMPLETED")
        return transfer, destination
    except Exception as exc:
        if temp_destination.exists():
            shutil.rmtree(temp_destination)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(exc)) from exc
