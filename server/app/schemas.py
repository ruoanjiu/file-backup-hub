from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ManifestFile(BaseModel):
    file_id: str
    original_path: str
    backup_path: str
    file_name: str
    file_type: str | None = None
    size: int
    mtime: float | None = None
    sha256: str
    possibly_active: bool = False


class Manifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str
    backup_id: str
    machine_id: str
    strategy_name: str
    created_at: datetime | None = None
    files: list[ManifestFile] = Field(default_factory=list)


class BackupInitRequest(BaseModel):
    backup_id: str
    machine_id: str
    strategy_name: str
    created_at: datetime
    file_count: int = Field(ge=0)
    total_size: int = Field(ge=0)
    bundle_size: int | None = Field(default=None, ge=0)
    bundle_sha256: str
    manifest: Manifest


class BackupInitResponse(BaseModel):
    backup_id: str
    status: str
    upload_url: str


class BundleUploadResponse(BaseModel):
    backup_id: str
    status: str
    bundle_sha256: str


class BackupListItem(BaseModel):
    backup_id: str
    machine_id: str
    strategy_name: str
    status: str
    created_at: str
    uploaded_at: str | None = None
    file_count: int
    total_size: int
    bundle_size: int | None = None
    bundle_sha256: str | None = None


class BackupListResponse(BaseModel):
    items: list[BackupListItem]
    limit: int
    offset: int
    total: int


class HealthResponse(BaseModel):
    status: str
    app: str


JsonObject = dict[str, Any]
