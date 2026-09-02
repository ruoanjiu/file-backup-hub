from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from client.app.config import AppConfig, TaskConfig

SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


@dataclass(frozen=True)
class ManifestFileEntry:
    file_id: str
    original_path: str
    backup_path: str
    file_name: str
    file_type: str
    size: int
    mtime: float
    sha256: str
    possibly_active: bool

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "original_path": self.original_path,
            "backup_path": self.backup_path,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "size": self.size,
            "mtime": self.mtime,
            "sha256": self.sha256,
            "possibly_active": self.possibly_active,
        }


def validate_safe_id(value: str, field_name: str) -> None:
    if not SAFE_ID_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} contains unsafe characters: {value}")


def infer_file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".log":
        return "log"
    if suffix == ".json":
        return "json"
    if suffix in {".xls", ".xlsx", ".xlsm"}:
        return "excel"
    return suffix.removeprefix(".") or "unknown"


def generate_backup_id(
    machine_id: str,
    task_name: str,
    created_at: datetime,
    uuid_fragment: str,
) -> str:
    validate_safe_id(machine_id, "machine_id")
    validate_safe_id(task_name, "task_name")
    validate_safe_id(uuid_fragment, "uuid_fragment")
    timestamp = created_at.strftime("%Y%m%d_%H%M%S")
    backup_id = f"{machine_id}__{task_name}__{timestamp}__{uuid_fragment[:8]}"
    validate_safe_id(backup_id, "backup_id")
    return backup_id


def now_for_config(config: AppConfig) -> datetime:
    return datetime.now(ZoneInfo(config.client.timezone))


def build_manifest(
    config: AppConfig,
    task: TaskConfig,
    backup_id: str,
    created_at: datetime,
    files: list[ManifestFileEntry],
) -> dict:
    total_size = sum(file_entry.size for file_entry in files)
    roots = [str(root.path) for root in task.roots]
    return {
        "schema_version": "1.0",
        "backup_id": backup_id,
        "machine_id": config.client.machine_id,
        "task_name": task.name,
        "created_at": created_at.isoformat(),
        "timezone": config.client.timezone,
        "source_hostname": socket.gethostname(),
        "archive_format": config.backup.archive_format,
        "file_count": len(files),
        "total_size": total_size,
        "roots": roots,
        "files": [file_entry.to_dict() for file_entry in files],
    }
