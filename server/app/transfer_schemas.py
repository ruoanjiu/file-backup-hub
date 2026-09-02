from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TransferFile(BaseModel):
    file_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    relative_path: str = Field(min_length=1, max_length=2048)
    backup_path: str = Field(min_length=1, max_length=2048)
    file_name: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TransferManifest(BaseModel):
    schema_version: str
    transfer_id: str
    sender_device_id: str
    receiver_device_id: str
    created_at: datetime
    file_count: int = Field(ge=1)
    total_size: int = Field(ge=0)
    files: list[TransferFile] = Field(min_length=1)


class TransferInitRequest(BaseModel):
    transfer_id: str
    sender_device_id: str
    receiver_device_id: str
    file_count: int = Field(ge=1)
    total_size: int = Field(ge=0)
    bundle_size: int = Field(ge=1)
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_in_hours: int = Field(default=24, ge=1, le=168)
    manifest: TransferManifest


class TransferItem(BaseModel):
    transfer_id: str
    sender_device_id: str
    receiver_device_id: str
    status: str
    created_at: str
    updated_at: str
    expires_at: str
    file_count: int
    total_size: int
    bundle_size: int
    bundle_sha256: str


class TransferListResponse(BaseModel):
    items: list[TransferItem]
    total: int
