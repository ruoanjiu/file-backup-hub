from __future__ import annotations

from fastapi import APIRouter, Depends

from server.app.config import Settings, get_settings
from server.app.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(status="ok", app=settings.app_name, server_id=settings.server_id)
