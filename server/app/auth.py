from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.app.config import Settings, get_settings
from server.app.database import get_db
from server.app.models import Client
from server.app.security import verify_secret
from server.app.utils.time import utc_now_iso


bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Actor:
    actor_id: str
    machine_id: str | None
    is_admin: bool = False

    @property
    def actor_type(self) -> str:
        return "admin" if self.is_admin else "client"


def _matches(value: str, expected: str) -> bool:
    return hmac.compare_digest(value.encode("utf-8"), expected.encode("utf-8"))


def get_current_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> Actor:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    token = credentials.credentials
    if settings.server_admin_token and _matches(token, settings.server_admin_token):
        return Actor(actor_id="admin", machine_id=None, is_admin=True)

    for machine_id, expected_token in settings.client_tokens.items():
        if _matches(token, expected_token):
            return Actor(actor_id=machine_id, machine_id=machine_id)

    token_parts = token.split(":", 2)
    if len(token_parts) == 3 and token_parts[0] == "fb1":
        machine_id = token_parts[1]
        client = db.scalar(
            select(Client).where(
                Client.machine_id == machine_id,
                Client.enabled == 1,
            )
        )
        if client is not None and verify_secret(token, client.token_hash):
            client.last_seen_at = utc_now_iso()
            db.commit()
            return Actor(actor_id=machine_id, machine_id=machine_id)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid bearer token",
    )


def ensure_machine_access(actor: Actor, machine_id: str) -> None:
    if actor.is_admin:
        return
    if actor.machine_id != machine_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token is not allowed to access this machine_id",
        )
