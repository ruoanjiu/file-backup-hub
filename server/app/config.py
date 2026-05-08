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
    app_env: str = "development"
    database_url: str = "sqlite:///./data/app.sqlite"
    storage_root: Path = Path("./data/storage")
    manifest_root: Path = Path("./data/manifests")
    max_upload_size_mb: int = 2048
    default_retention_days: int = 90
    server_admin_token: str = ""
    client_tokens: dict[str, str] = field(default_factory=dict)

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            app_name=os.getenv("APP_NAME", cls.app_name),
            app_env=os.getenv("APP_ENV", cls.app_env),
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            storage_root=Path(os.getenv("STORAGE_ROOT", str(cls.storage_root))),
            manifest_root=Path(os.getenv("MANIFEST_ROOT", str(cls.manifest_root))),
            max_upload_size_mb=int(os.getenv("MAX_UPLOAD_SIZE_MB", str(cls.max_upload_size_mb))),
            default_retention_days=int(
                os.getenv("DEFAULT_RETENTION_DAYS", str(cls.default_retention_days))
            ),
            server_admin_token=os.getenv("SERVER_ADMIN_TOKEN", ""),
            client_tokens=_parse_client_tokens(os.getenv("CLIENT_TOKENS", "")),
        )


def get_settings() -> Settings:
    return Settings.from_env()
