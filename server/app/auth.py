from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from server.app.config import Settings, get_settings


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
