from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from server.app.database import Base


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    machine_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_at: Mapped[str | None] = mapped_column(String(64))


class Backup(Base):
    __tablename__ = "backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backup_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    machine_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_at: Mapped[str | None] = mapped_column(String(64))
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bundle_size: Mapped[int | None] = mapped_column(Integer)
    bundle_sha256: Mapped[str | None] = mapped_column(String(64))
    storage_path: Mapped[str | None] = mapped_column(Text)
    manifest_path: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)


class BackupFile(Base):
    __tablename__ = "backup_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backup_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_id: Mapped[str] = mapped_column(String(64), nullable=False)
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    backup_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str | None] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    mtime: Mapped[float | None]
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    possibly_active: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    detail_json: Mapped[str | None] = mapped_column(Text)


class PairingCode(Base):
    __tablename__ = "pairing_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transfer_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    sender_device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    receiver_device_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(64), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_size: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle_size: Mapped[int] = mapped_column(Integer, nullable=False)
    bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_path: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
