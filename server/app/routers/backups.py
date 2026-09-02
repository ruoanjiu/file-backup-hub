from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from server.app.auth import Actor, get_current_actor
from server.app.config import Settings, get_settings
from server.app.database import get_db
from server.app.schemas import (
    BackupInitRequest,
    BackupInitResponse,
    BackupListItem,
    BackupListResponse,
    BundleUploadResponse,
)
from server.app.services.backup_service import (
    backup_to_list_item,
    delete_backup,
    get_backup_for_actor,
    init_backup,
    list_backups,
    load_manifest_for_backup,
    mark_upload_completed,
    mark_upload_failed,
    mark_uploading,
)
from server.app.storage import save_bundle_stream

router = APIRouter(prefix="/api/v1/backups", tags=["backups"])


@router.post("/init", response_model=BackupInitResponse, status_code=status.HTTP_201_CREATED)
def initialize_backup(
    payload: BackupInitRequest,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
    settings: Settings = Depends(get_settings),
) -> BackupInitResponse:
    backup = init_backup(db, payload, actor, settings)
    return BackupInitResponse(
        backup_id=backup.backup_id,
        status=backup.status,
        upload_url=f"/api/v1/backups/{backup.backup_id}/bundle",
    )


@router.put("/{backup_id}/bundle", response_model=BundleUploadResponse)
async def upload_bundle(
    backup_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
    settings: Settings = Depends(get_settings),
) -> BundleUploadResponse:
    backup = get_backup_for_actor(db, backup_id, actor)
    if backup.bundle_sha256 is None or backup.storage_path is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Backup was not initialized with bundle metadata",
        )

    mark_uploading(db, backup)
    try:
        actual_sha256, total_bytes = await save_bundle_stream(
            request.stream(),
            backup.bundle_sha256,
            Path(backup.storage_path),
            settings.max_upload_size_bytes,
        )
    except Exception as exc:
        mark_upload_failed(db, backup, str(exc), actor)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    mark_upload_completed(db, backup, actual_sha256, total_bytes, actor)
    return BundleUploadResponse(
        backup_id=backup.backup_id,
        status=backup.status,
        bundle_sha256=actual_sha256,
    )


@router.get("", response_model=BackupListResponse)
def get_backups(
    machine_id: str | None = None,
    task_name: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> BackupListResponse:
    return list_backups(db, actor, machine_id, task_name, limit, offset)


@router.get("/{backup_id}", response_model=BackupListItem)
def get_backup_metadata(
    backup_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> BackupListItem:
    backup = get_backup_for_actor(db, backup_id, actor)
    return backup_to_list_item(backup)


@router.get("/{backup_id}/manifest")
def get_manifest(
    backup_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> dict:
    backup = get_backup_for_actor(db, backup_id, actor)
    return load_manifest_for_backup(backup)


@router.get("/{backup_id}/bundle")
def download_bundle(
    backup_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> FileResponse:
    backup = get_backup_for_actor(db, backup_id, actor)
    if backup.status != "COMPLETED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only COMPLETED backups can be downloaded",
        )
    if backup.storage_path is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Backup storage path is missing",
        )
    path = Path(backup.storage_path)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bundle file not found")
    return FileResponse(
        path=path,
        media_type="application/gzip",
        filename="bundle.tar.gz",
    )


@router.delete("/{backup_id}")
def delete_backup_route(
    backup_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    if not settings.allow_backup_delete:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Backup deletion is disabled on this Server",
        )
    if not actor.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Server administrator can delete backup copies",
        )
    backup = get_backup_for_actor(db, backup_id, actor)
    delete_backup(db, backup, actor, settings)
    return {"backup_id": backup_id, "status": "DELETED"}
