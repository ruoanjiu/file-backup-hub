from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from server.app.auth import Actor, get_current_actor
from server.app.config import Settings, get_settings
from server.app.database import get_db
from server.app.services.device_service import (
    create_pairing_code,
    list_devices,
    pair_device,
    rename_device,
    revoke_device,
)


router = APIRouter(prefix="/api/v1", tags=["devices"])


class PairingCodeRequest(BaseModel):
    lifetime_minutes: int = Field(default=5, ge=1, le=30)


class PairDeviceRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)
    device_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)


class RenameDeviceRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)


@router.post("/pairing/codes")
def new_pairing_code(
    payload: PairingCodeRequest,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
    settings: Settings = Depends(get_settings),
) -> dict:
    record, code = create_pairing_code(
        db,
        actor,
        lifetime_minutes=payload.lifetime_minutes,
    )
    return {
        "code": code,
        "expires_at": record.expires_at,
        "server_id": settings.server_id,
    }


@router.post("/devices/pair")
def pair_new_device(
    payload: PairDeviceRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    client, token = pair_device(
        db,
        code=payload.code,
        machine_id=payload.device_id,
        display_name=payload.display_name,
    )
    return {
        "device_id": client.machine_id,
        "display_name": client.display_name,
        "token": token,
        "server_id": settings.server_id,
    }


@router.get("/devices")
def get_devices(
    db: Session = Depends(get_db),
    _: Actor = Depends(get_current_actor),
    settings: Settings = Depends(get_settings),
) -> dict:
    return {"items": list_devices(db, settings)}


@router.post("/devices/self/revoke")
def revoke_current_device(
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> dict:
    if actor.is_admin or actor.machine_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A paired device token is required",
        )
    client = revoke_device(
        db,
        actor,
        machine_id=actor.machine_id,
    )
    return {
        "device_id": client.machine_id,
        "status": "REVOKED",
        "enabled": bool(client.enabled),
    }


@router.post("/devices/{device_id}/revoke")
def revoke_paired_device(
    device_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> dict:
    client = revoke_device(
        db,
        actor,
        machine_id=device_id,
        admin_only=True,
    )
    return {
        "device_id": client.machine_id,
        "display_name": client.display_name,
        "status": "REVOKED",
        "enabled": bool(client.enabled),
    }


@router.patch("/devices/{device_id}")
def update_device(
    device_id: str,
    payload: RenameDeviceRequest,
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_current_actor),
) -> dict:
    client = rename_device(
        db,
        actor,
        machine_id=device_id,
        display_name=payload.display_name,
    )
    return {
        "device_id": client.machine_id,
        "display_name": client.display_name,
        "enabled": bool(client.enabled),
        "last_seen_at": client.last_seen_at,
    }
