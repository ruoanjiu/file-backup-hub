from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.auth import Actor
from server.app.config import Settings
from server.app.models import AuditLog, Client, PairingCode
from server.app.security import hash_pairing_code, hash_secret
from server.app.utils.time import utc_now_iso


SAFE_DEVICE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _audit_device(
    db: Session,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    machine_id: str,
) -> None:
    db.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target=machine_id,
            ip_address=None,
            created_at=utc_now_iso(),
            detail_json=None,
        )
    )


def _validate_device_id(machine_id: str) -> str:
    machine_id = machine_id.strip()
    if not SAFE_DEVICE_ID.fullmatch(machine_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="device_id may contain only letters, numbers, dot, underscore and hyphen",
        )
    return machine_id


def _validate_display_name(display_name: str) -> str:
    display_name = display_name.strip()
    if not display_name or len(display_name) > 80:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="display_name must contain 1 to 80 characters",
        )
    return display_name


def create_pairing_code(
    db: Session,
    actor: Actor,
    *,
    lifetime_minutes: int = 5,
) -> tuple[PairingCode, str]:
    if not actor.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin token required")
    lifetime_minutes = max(1, min(lifetime_minutes, 30))
    now = datetime.now(UTC)
    for _ in range(20):
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = hash_pairing_code(code)
        if db.scalar(select(PairingCode).where(PairingCode.code_hash == code_hash)) is None:
            record = PairingCode(
                code_hash=code_hash,
                created_at=now.isoformat(),
                expires_at=(now + timedelta(minutes=lifetime_minutes)).isoformat(),
                used_at=None,
                created_by=actor.actor_id,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return record, code
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Unable to allocate a unique pairing code",
    )


def pair_device(
    db: Session,
    *,
    code: str,
    machine_id: str,
    display_name: str,
) -> tuple[Client, str]:
    machine_id = _validate_device_id(machine_id)
    display_name = _validate_display_name(display_name)
    record = db.scalar(
        select(PairingCode).where(PairingCode.code_hash == hash_pairing_code(code.strip()))
    )
    now = datetime.now(UTC)
    if record is None or record.used_at is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pairing code is invalid or already used")
    if datetime.fromisoformat(record.expires_at) < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pairing code has expired")
    existing = db.scalar(select(Client).where(Client.machine_id == machine_id))
    if existing is not None and bool(existing.enabled):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="device_id is already paired")

    token = f"fb1:{machine_id}:{secrets.token_urlsafe(32)}"
    now_text = now.isoformat()
    if existing is None:
        client = Client(
            machine_id=machine_id,
            display_name=display_name,
            token_hash=hash_secret(token),
            enabled=1,
            created_at=now_text,
            updated_at=now_text,
            last_seen_at=now_text,
        )
        db.add(client)
        audit_action = "device.paired"
    else:
        client = existing
        client.display_name = display_name
        client.token_hash = hash_secret(token)
        client.enabled = 1
        client.updated_at = now_text
        client.last_seen_at = now_text
        audit_action = "device.reactivated"
    record.used_at = now_text
    _audit_device(
        db,
        actor_type="admin",
        actor_id=record.created_by,
        action=audit_action,
        machine_id=machine_id,
    )
    db.commit()
    db.refresh(client)
    return client, token


def list_devices(db: Session, settings: Settings) -> list[dict]:
    dynamic = db.scalars(select(Client).order_by(Client.display_name, Client.machine_id)).all()
    items = [
        {
            "device_id": client.machine_id,
            "display_name": client.display_name or client.machine_id,
            "enabled": bool(client.enabled),
            "paired": True,
            "last_seen_at": client.last_seen_at,
        }
        for client in dynamic
    ]
    existing = {item["device_id"] for item in items}
    for machine_id in sorted(settings.client_tokens):
        if machine_id not in existing:
            items.append(
                {
                    "device_id": machine_id,
                    "display_name": machine_id,
                    "enabled": True,
                    "paired": False,
                    "last_seen_at": None,
                }
            )
    return items


def rename_device(
    db: Session,
    actor: Actor,
    *,
    machine_id: str,
    display_name: str,
) -> Client:
    machine_id = _validate_device_id(machine_id)
    display_name = _validate_display_name(display_name)
    if not actor.is_admin and actor.machine_id != machine_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A device may rename only itself",
        )
    client = db.scalar(select(Client).where(Client.machine_id == machine_id))
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Paired device not found",
        )
    client.display_name = display_name
    client.updated_at = utc_now_iso()
    db.commit()
    db.refresh(client)
    return client


def revoke_device(
    db: Session,
    actor: Actor,
    *,
    machine_id: str,
    admin_only: bool = False,
) -> Client:
    machine_id = _validate_device_id(machine_id)
    if admin_only and not actor.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin token required",
        )
    if not actor.is_admin and actor.machine_id != machine_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="A device may revoke only itself",
        )
    client = db.scalar(select(Client).where(Client.machine_id == machine_id))
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Paired device not found",
        )
    if bool(client.enabled):
        client.enabled = 0
        client.updated_at = utc_now_iso()
        _audit_device(
            db,
            actor_type=actor.actor_type,
            actor_id=actor.actor_id,
            action="device.revoked.admin" if actor.is_admin else "device.revoked.self",
            machine_id=machine_id,
        )
        db.commit()
        db.refresh(client)
    return client


def remove_revoked_device(
    db: Session,
    actor: Actor,
    *,
    machine_id: str,
) -> str:
    machine_id = _validate_device_id(machine_id)
    if not actor.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin token required",
        )
    client = db.scalar(select(Client).where(Client.machine_id == machine_id))
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Paired device not found",
        )
    if bool(client.enabled):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Revoke the device before removing its record",
        )
    db.delete(client)
    _audit_device(
        db,
        actor_type=actor.actor_type,
        actor_id=actor.actor_id,
        action="device.record.removed",
        machine_id=machine_id,
    )
    db.commit()
    return machine_id
