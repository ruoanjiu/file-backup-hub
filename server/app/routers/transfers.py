from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from server.app.auth import Actor, get_current_actor
from server.app.config import Settings, get_settings
from server.app.database import get_db
from server.app.services.transfer_service import (
    get_transfer_for_actor,
    init_transfer,
    list_inbox,
    receive_transfer_on_server,
    SERVER_RECEIVER_ID,
    set_transfer_status,
    transfer_to_item,
)
from server.app.storage import read_json, save_bundle_stream
from server.app.transfer_schemas import TransferInitRequest, TransferItem, TransferListResponse
from server.app.utils.time import utc_now_iso


router = APIRouter(prefix="/api/v1/transfers", tags=["transfers"])


@router.post("/init", response_model=TransferItem, status_code=201)
def initialize_transfer(
    payload: TransferInitRequest,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
    settings: Settings = Depends(get_settings),
) -> TransferItem:
    return transfer_to_item(init_transfer(db, actor, settings, payload))


@router.get("/inbox", response_model=TransferListResponse)
def inbox(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> TransferListResponse:
    return list_inbox(db, actor, limit)


@router.put("/{transfer_id}/bundle", response_model=TransferItem)
async def upload_transfer_bundle(
    transfer_id: str,
    request: Request,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
    settings: Settings = Depends(get_settings),
) -> TransferItem:
    transfer = get_transfer_for_actor(db, transfer_id, actor)
    if not actor.is_admin and actor.machine_id != transfer.sender_device_id:
        raise HTTPException(status_code=403, detail="Only the sender can upload this transfer")
    if transfer.status in {"AVAILABLE", "ACCEPTED", "COMPLETED"}:
        return transfer_to_item(transfer)
    if transfer.status not in {"PENDING", "FAILED"}:
        raise HTTPException(status_code=409, detail=f"Transfer is not uploadable from {transfer.status}")
    transfer.status = "UPLOADING"
    transfer.updated_at = utc_now_iso()
    db.commit()
    try:
        actual_sha256, total_bytes = await save_bundle_stream(
            request.stream(),
            transfer.bundle_sha256,
            Path(transfer.storage_path),
            settings.max_transfer_size_bytes,
        )
        if total_bytes != transfer.bundle_size:
            raise ValueError("Uploaded transfer size does not match initialized metadata")
    except Exception as exc:
        transfer.status = "FAILED"
        transfer.error_message = str(exc)
        transfer.updated_at = utc_now_iso()
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    transfer.status = "AVAILABLE"
    transfer.bundle_sha256 = actual_sha256
    transfer.error_message = None
    transfer.updated_at = utc_now_iso()
    db.commit()
    db.refresh(transfer)
    return transfer_to_item(transfer)


@router.get("/{transfer_id}", response_model=TransferItem)
def transfer_metadata(
    transfer_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> TransferItem:
    return transfer_to_item(get_transfer_for_actor(db, transfer_id, actor))


@router.get("/{transfer_id}/manifest")
def transfer_manifest(
    transfer_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> dict:
    transfer = get_transfer_for_actor(db, transfer_id, actor)
    return read_json(Path(transfer.manifest_path))


@router.get("/{transfer_id}/bundle")
def download_transfer_bundle(
    transfer_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> FileResponse:
    transfer = get_transfer_for_actor(db, transfer_id, actor)
    if not actor.is_admin and actor.machine_id == transfer.receiver_device_id:
        if transfer.status not in {"ACCEPTED", "COMPLETED"}:
            raise HTTPException(status_code=409, detail="Receiver must accept the transfer first")
    elif transfer.status not in {"AVAILABLE", "ACCEPTED", "COMPLETED"}:
        raise HTTPException(status_code=409, detail="Transfer bundle is not available")
    path = Path(transfer.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Transfer bundle file is missing")
    return FileResponse(path, media_type="application/gzip", filename=f"{transfer_id}.tar.gz")


@router.post("/{transfer_id}/accept", response_model=TransferItem)
def accept_transfer(
    transfer_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> TransferItem:
    transfer = get_transfer_for_actor(db, transfer_id, actor)
    return transfer_to_item(set_transfer_status(db, transfer, actor, "ACCEPTED"))


@router.post("/{transfer_id}/complete", response_model=TransferItem)
def complete_transfer(
    transfer_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> TransferItem:
    transfer = get_transfer_for_actor(db, transfer_id, actor)
    return transfer_to_item(set_transfer_status(db, transfer, actor, "COMPLETED"))


@router.post("/{transfer_id}/reject", response_model=TransferItem)
def reject_transfer(
    transfer_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> TransferItem:
    transfer = get_transfer_for_actor(db, transfer_id, actor)
    return transfer_to_item(set_transfer_status(db, transfer, actor, "REJECTED"))


@router.post("/{transfer_id}/receive-on-server")
def receive_on_server(
    transfer_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
    settings: Settings = Depends(get_settings),
) -> dict:
    transfer = get_transfer_for_actor(db, transfer_id, actor)
    completed, destination, received_count = receive_transfer_on_server(
        db,
        transfer,
        actor,
        settings,
    )
    return {
        "transfer_id": completed.transfer_id,
        "receiver_device_id": SERVER_RECEIVER_ID,
        "status": completed.status,
        "received_count": received_count,
        "destination": str(destination),
        "source_files_deleted": False,
        "transfer_bundle_deleted": False,
    }
