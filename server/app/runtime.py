from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ServerRuntimeConfig:
    server_id: str
    host: str
    port: int
    data_dir: Path
    admin_token: str
    client_tokens: str
    allow_backup_delete: bool = False

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'db' / 'app.sqlite'}"

    @property
    def pid_file(self) -> Path:
        return self.data_dir / "server.pid"


def default_server_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "FileBackupServer"
    if sys.platform == "darwin":
        return Path("/Users/Shared/FileBackupServer")
    return Path("/var/lib/file-backup-server")


def default_server_config_path() -> Path:
    return default_server_data_dir() / "config" / "server.json"


def new_default_config() -> ServerRuntimeConfig:
    return ServerRuntimeConfig(
        server_id="server-1",
        host="0.0.0.0",
        port=8000,
        data_dir=default_server_data_dir(),
        admin_token=secrets.token_urlsafe(32),
        client_tokens="",
        allow_backup_delete=False,
    )


def save_runtime_config(path: Path, config: ServerRuntimeConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(config)
    data["data_dir"] = str(config.data_dir)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        temp_path.chmod(0o600)
    temp_path.replace(path)


def load_runtime_config(path: Path) -> ServerRuntimeConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ServerRuntimeConfig(
        server_id=str(data["server_id"]),
        host=str(data.get("host", "0.0.0.0")),
        port=int(data.get("port", 8000)),
        data_dir=Path(data["data_dir"]).expanduser(),
        admin_token=str(data["admin_token"]),
        client_tokens=str(data["client_tokens"]),
        allow_backup_delete=bool(data.get("allow_backup_delete", False)),
    )


def build_settings(config: ServerRuntimeConfig):
    from server.app.config import Settings, _parse_client_tokens

    return Settings(
        server_id=config.server_id,
        app_env="production",
        database_url=config.database_url,
        storage_root=config.data_dir / "storage",
        manifest_root=config.data_dir / "manifests",
        trash_root=config.data_dir / "trash",
        transfer_root=config.data_dir / "transfers",
        server_admin_token=config.admin_token,
        client_tokens=_parse_client_tokens(config.client_tokens),
        allow_backup_delete=config.allow_backup_delete,
    )


def health_url(config: ServerRuntimeConfig) -> str:
    host = "127.0.0.1" if config.host in {"0.0.0.0", "::"} else config.host
    return f"http://{host}:{config.port}/health"


def read_health(config: ServerRuntimeConfig, timeout: float = 2) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(health_url(config), timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def read_pid(config: ServerRuntimeConfig) -> int | None:
    try:
        return int(config.pid_file.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def stop_server_process(config: ServerRuntimeConfig) -> bool:
    health = read_health(config)
    if not health or health.get("server_id") != config.server_id:
        return False
    pid = read_pid(config)
    if pid is None:
        return False
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        return completed.returncode == 0
    os.kill(pid, signal.SIGTERM)
    return True
