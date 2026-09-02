from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import json
import urllib.request
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import webview

from server.app.runtime import (
    ServerRuntimeConfig,
    default_server_config_path,
    load_runtime_config,
    new_default_config,
    read_health,
    save_runtime_config,
    stop_server_process,
)


SAFE_SERVER_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def api_request(
    config: ServerRuntimeConfig,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"http://127.0.0.1:{config.port}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def frontend_index() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "frontend" / "dist" / "index.html"


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def format_size(value: int) -> str:
    size = float(value)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def storage_directories(config: ServerRuntimeConfig) -> dict[str, Path]:
    return {
        "storage": config.data_dir / "storage",
        "manifests": config.data_dir / "manifests",
        "transfers": config.data_dir / "transfers",
        "trash": config.data_dir / "trash",
    }


def resolve_storage_directory(config: ServerRuntimeConfig, requested_path: str) -> Path:
    requested = Path(requested_path).expanduser().resolve()
    allowed = {path.resolve() for path in storage_directories(config).values()}
    if requested not in allowed:
        raise ValueError("Only managed Server storage directories may be opened")
    return requested


def open_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def validate_server_id(server_id: str) -> str:
    value = server_id.strip()
    if not SAFE_SERVER_ID.fullmatch(value):
        raise ValueError(
            "Server ID must contain only letters, numbers, dot, underscore and hyphen"
        )
    return value


def validate_server_data_dir(raw_path: str) -> Path:
    value = raw_path.strip()
    if not value:
        raise ValueError("Server data directory is required")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Server data directory must be an absolute path")
    path = path.resolve()
    anchor = Path(path.anchor)
    if path == anchor:
        raise ValueError("A disk or filesystem root cannot be used as the data directory")
    if path.exists() and not path.is_dir():
        raise ValueError("Server data directory points to a file")
    parent = path
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.exists() or not os.access(parent, os.W_OK):
        raise ValueError("Server data directory is not writable")
    return path


def backup_runtime_config(config_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_dir = config_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{config_path.stem}-before-settings-{timestamp}.json"
    shutil.copy2(config_path, backup_path)
    if os.name != "nt":
        backup_path.chmod(0o600)
    return backup_path


def wait_for_server_health(
    config: ServerRuntimeConfig,
    *,
    running: bool,
    timeout: float = 30,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health = read_health(config, timeout=0.5)
        matches = bool(health and health.get("server_id") == config.server_id)
        if matches is running:
            return True
        time.sleep(0.2)
    return False


class ServerDesktopApi:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or default_server_config_path()
        self.window: Any | None = None
        if self.config_path.exists():
            self.config = load_runtime_config(self.config_path)
        else:
            self.config = new_default_config()
            save_runtime_config(self.config_path, self.config)

    def bootstrap(self) -> dict[str, Any]:
        health = read_health(self.config, timeout=0.5)
        devices: list[dict[str, Any]] = []
        inbox: list[dict[str, Any]] = []
        if health:
            try:
                devices = api_request(
                    self.config,
                    "/api/v1/devices",
                    token=self.config.admin_token,
                ).get("items", [])
            except Exception:
                devices = []
            try:
                inbox = api_request(
                    self.config,
                    "/api/v1/transfers/inbox?limit=100",
                    token=self.config.admin_token,
                ).get("items", [])
            except Exception:
                inbox = []
        storage = []
        for key, name in [
            ("storage", "备份包"),
            ("manifests", "Manifest"),
            ("transfers", "文件中转"),
            ("trash", "Trash"),
        ]:
            path = storage_directories(self.config)[key]
            storage.append(
                {"name": name, "path": str(path), "size": format_size(directory_size(path))}
            )
        return {
            "mode": "server",
            "configured": True,
            "server": {
                "id": self.config.server_id,
                "status": "ok" if health and health.get("server_id") == self.config.server_id else "offline",
                "endpoint": f"http://127.0.0.1:{self.config.port}",
                "data_dir": str(self.config.data_dir),
            },
            "devices": devices,
            "inbox": inbox,
            "storage": storage,
        }

    def start_server(self) -> dict[str, str]:
        health = read_health(self.config)
        if health and health.get("server_id") == self.config.server_id:
            return {"status": "RUNNING"}
        if health:
            raise RuntimeError(
                f"Port {self.config.port} is already used by another File Backup Server"
            )
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--headless", "--config", str(self.config_path)]
        else:
            command = [
                sys.executable,
                str(Path(__file__).resolve().parents[2] / "run_server_app.py"),
                "--headless",
                "--config",
                str(self.config_path),
            ]
        log_dir = self.config.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = (log_dir / "server.log").open("a", encoding="utf-8")
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": log_file,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen(command, **kwargs)
        finally:
            log_file.close()
        return {"status": "STARTING"}

    def stop_server(self) -> dict[str, str]:
        return {"status": "STOPPING" if stop_server_process(self.config) else "NOT_RUNNING"}

    def open_storage_path(self, requested_path: str) -> dict[str, str]:
        path = resolve_storage_directory(self.config, requested_path)
        open_directory(path)
        return {"status": "OPENED", "path": str(path)}

    def choose_server_data_dir(self, current_path: str = "") -> list[str]:
        if self.window is None:
            return []
        result = self.window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=current_path or str(self.config.data_dir),
        )
        return [str(item) for item in (result or [])]

    def save_server_settings(self, server_id: str, data_dir: str) -> dict[str, Any]:
        new_server_id = validate_server_id(server_id)
        new_data_dir = validate_server_data_dir(data_dir)
        old_config = self.config
        if (
            new_server_id == old_config.server_id
            and new_data_dir == old_config.data_dir.resolve()
        ):
            return {
                "status": "UNCHANGED",
                "server_id": old_config.server_id,
                "data_dir": str(old_config.data_dir),
                "restarted": False,
            }

        health = read_health(old_config)
        if health and health.get("server_id") != old_config.server_id:
            raise RuntimeError(
                f"Port {old_config.port} is already used by another File Backup Server"
            )
        was_running = bool(health)
        if was_running:
            if not stop_server_process(old_config):
                raise RuntimeError("Unable to stop the current Server safely")
            if not wait_for_server_health(old_config, running=False, timeout=15):
                raise RuntimeError("Timed out while stopping the current Server")

        new_config = replace(
            old_config,
            server_id=new_server_id,
            data_dir=new_data_dir,
        )
        backup_path: Path | None = None
        try:
            backup_path = backup_runtime_config(self.config_path)
            save_runtime_config(self.config_path, new_config)
            self.config = new_config
            if was_running:
                self.start_server()
                if not wait_for_server_health(new_config, running=True):
                    raise RuntimeError("The Server did not start with the new settings")
        except Exception as error:
            save_runtime_config(self.config_path, old_config)
            self.config = old_config
            recovery_error: Exception | None = None
            if was_running and not read_health(old_config):
                try:
                    self.start_server()
                    if not wait_for_server_health(old_config, running=True):
                        raise RuntimeError("The previous Server settings could not be restarted")
                except Exception as recovery:
                    recovery_error = recovery
            if recovery_error:
                raise RuntimeError(
                    f"Settings failed and automatic recovery also failed: {recovery_error}"
                ) from error
            raise RuntimeError(
                f"Settings were not applied; the previous configuration was restored: {error}"
            ) from error

        return {
            "status": "UPDATED",
            "server_id": new_config.server_id,
            "data_dir": str(new_config.data_dir),
            "restarted": was_running,
            "config_backup": str(backup_path) if backup_path else "",
        }

    def create_pairing_code(self) -> dict[str, Any]:
        return api_request(
            self.config,
            "/api/v1/pairing/codes",
            method="POST",
            token=self.config.admin_token,
            payload={"lifetime_minutes": 5},
        )

    def rename_device(self, device_id: str, display_name: str) -> dict[str, Any]:
        return api_request(
            self.config,
            f"/api/v1/devices/{device_id}",
            method="PATCH",
            token=self.config.admin_token,
            payload={"display_name": display_name},
        )

    def revoke_device(self, device_id: str) -> dict[str, Any]:
        return api_request(
            self.config,
            f"/api/v1/devices/{device_id}/revoke",
            method="POST",
            token=self.config.admin_token,
        )

    def remove_device(self, device_id: str) -> dict[str, Any]:
        return api_request(
            self.config,
            f"/api/v1/devices/{device_id}",
            method="DELETE",
            token=self.config.admin_token,
        )

    def receive_server_transfer(self, transfer_id: str) -> dict[str, Any]:
        return api_request(
            self.config,
            f"/api/v1/transfers/{transfer_id}/receive-on-server",
            method="POST",
            token=self.config.admin_token,
        )

    def reject_server_transfer(self, transfer_id: str) -> dict[str, Any]:
        response = api_request(
            self.config,
            f"/api/v1/transfers/{transfer_id}/reject",
            method="POST",
            token=self.config.admin_token,
        )
        return {
            **response,
            "source_files_deleted": False,
            "transfer_bundle_deleted": False,
        }


def run_server_webview(config_path: Path | None = None) -> None:
    index = frontend_index()
    if not index.exists():
        raise FileNotFoundError(f"Vue frontend has not been built: {index}")
    api = ServerDesktopApi(config_path)
    window = webview.create_window(
        "File Backup Server",
        index.as_uri() + "?mode=server",
        js_api=api,
        width=1240,
        height=780,
        min_size=(980, 680),
        background_color="#f3f5f8",
    )
    api.window = window
    if sys.platform == "darwin":
        from server.app.macos_menu_bar import MacOSServerMenuBar

        menu_bar = MacOSServerMenuBar(window, api)
        window.events.closing += menu_bar.handle_window_closing
        webview.start(menu_bar.install, debug=False)
    else:
        webview.start(debug=False)
