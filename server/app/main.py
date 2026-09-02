from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from server.app.config import Settings, get_settings
from server.app.database import configure_database, init_db
from server.app.routers import admin_ui, backups, devices, health, transfers


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        active_settings = settings or get_settings()
        active_settings.storage_root.mkdir(parents=True, exist_ok=True)
        active_settings.manifest_root.mkdir(parents=True, exist_ok=True)
        active_settings.trash_root.mkdir(parents=True, exist_ok=True)
        active_settings.transfer_root.mkdir(parents=True, exist_ok=True)
        configure_database(active_settings.database_url)
        init_db()
        yield

    app = FastAPI(title="file-backup-server", lifespan=lifespan)

    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings

    app.include_router(health.router)
    app.include_router(admin_ui.router)
    app.include_router(backups.router)
    app.include_router(devices.router)
    app.include_router(transfers.router)
    return app


app = create_app()
