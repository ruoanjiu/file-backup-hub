from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _parse_client_tokens(raw: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        machine_id, separator, token = item.partition(":")
        if not separator or not machine_id.strip() or not token:
            continue
        tokens[machine_id.strip()] = token
    return tokens


@dataclass(frozen=True)
class Settings:
    app_name: str = "file-backup-server"
    server_id: str = "server-1"
    app_env: str = "development"
    database_url: str = "sqlite:///./data/app.sqlite"
    storage_root: Path = Path("./data/storage")
    manifest_root: Path = Path("./data/manifests")
    trash_root: Path = Path("./data/trash")
    transfer_root: Path = Path("./data/transfers")
    max_upload_size_mb: int = 2048
    max_transfer_size_mb: int = 2048
    default_retention_days: int = 90
    server_admin_token: str = ""
    client_tokens: dict[str, str] = field(default_factory=dict)
    allow_backup_delete: bool = False

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def max_transfer_size_bytes(self) -> int:
        return self.max_transfer_size_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            app_name=os.getenv("APP_NAME", cls.app_name),
            server_id=os.getenv("SERVER_ID", cls.server_id),
            app_env=os.getenv("APP_ENV", cls.app_env),
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            storage_root=Path(os.getenv("STORAGE_ROOT", str(cls.storage_root))),
            manifest_root=Path(os.getenv("MANIFEST_ROOT", str(cls.manifest_root))),
            trash_root=Path(os.getenv("TRASH_ROOT", str(cls.trash_root))),
            transfer_root=Path(os.getenv("TRANSFER_ROOT", str(cls.transfer_root))),
            max_upload_size_mb=int(os.getenv("MAX_UPLOAD_SIZE_MB", str(cls.max_upload_size_mb))),
            max_transfer_size_mb=int(
                os.getenv("MAX_TRANSFER_SIZE_MB", str(cls.max_transfer_size_mb))
            ),
            default_retention_days=int(
                os.getenv("DEFAULT_RETENTION_DAYS", str(cls.default_retention_days))
            ),
            server_admin_token=os.getenv("SERVER_ADMIN_TOKEN", ""),
            client_tokens=_parse_client_tokens(os.getenv("CLIENT_TOKENS", "")),
            allow_backup_delete=os.getenv("ALLOW_BACKUP_DELETE", "false").lower()
            in {"1", "true", "yes", "on"},
        )


def get_settings() -> Settings:
    return Settings.from_env()
