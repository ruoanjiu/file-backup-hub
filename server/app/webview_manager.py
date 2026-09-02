from __future__ import annotations

import os
import re
import subprocess
import sys
import json
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

import webview

from desktop_tray import DesktopTrayController
from server.app.runtime import (
    ServerRuntimeConfig,
    default_server_config_path,
    load_runtime_config,
    new_default_config,
    read_health,
    save_runtime_config,
    stop_server_process,
)
from server.app.webview_runtime import configure_bundled_webview2_runtime


SERVER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


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


class ServerDesktopApi:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or default_server_config_path()
        self._window: Any | None = None
        if self.config_path.exists():
            self.config = load_runtime_config(self.config_path)
        else:
            self.config = new_default_config()
            save_runtime_config(self.config_path, self.config)

    def bootstrap(self) -> dict[str, Any]:
        health = read_health(self.config, timeout=0.5)
        devices: list[dict[str, Any]] = []
        server_inbox: list[dict[str, Any]] = []
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
                server_inbox = api_request(
                    self.config,
                    "/api/v1/transfers/server-inbox",
                    token=self.config.admin_token,
                ).get("items", [])
            except Exception:
                server_inbox = []
        storage = []
        for key, name in [
            ("storage", "备份包"),
            ("manifests", "Manifest"),
            ("transfers", "文件中转"),
            ("trash", "Trash"),
        ]:
            path = self.config.data_dir / key
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
            "server_inbox": server_inbox,
            "server_inbox_dir": str(
                self.config.data_dir / "transfers" / "server-inbox"
            ),
            "storage": storage,
        }

    def open_path(self, path: str) -> dict[str, Any]:
        allowed = {
            (self.config.data_dir / name).resolve()
            for name in ("storage", "manifests", "transfers", "trash")
        }
        allowed.add(
            (self.config.data_dir / "transfers" / "server-inbox").resolve()
        )
        target = Path(path).expanduser().resolve()
        if target not in allowed:
            raise ValueError("只能打开 File Backup Server 管理的存储目录")
        target.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(target))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        return {"opened": True, "path": str(target)}

    def choose_server_data_dir(self, directory: str = "") -> list[str]:
        if self._window is None:
            return []
        result = self._window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=directory or "",
        )
        return [str(item) for item in (result or [])]

    def save_server_settings(self, server_id: str, data_dir: str) -> dict[str, Any]:
        normalized_id = server_id.strip()
        if not SERVER_ID_PATTERN.fullmatch(normalized_id):
            raise ValueError("Server ID 只能包含字母、数字、点、下划线和短横线")

        raw_data_dir = data_dir.strip()
        if not raw_data_dir:
            raise ValueError("数据目录不能为空")
        target_dir = Path(raw_data_dir).expanduser()
        if not target_dir.is_absolute():
            raise ValueError("数据目录必须使用绝对路径")
        target_dir = target_dir.resolve()
        if target_dir == Path(target_dir.anchor):
            raise ValueError("不能直接使用磁盘根目录作为数据目录")
        if target_dir.exists() and not target_dir.is_dir():
            raise ValueError("数据目录指向了一个文件")
        target_dir.mkdir(parents=True, exist_ok=True)

        previous = self.config
        updated = replace(
            previous,
            server_id=normalized_id,
            data_dir=target_dir,
        )
        server_id_changed = updated.server_id != previous.server_id
        data_dir_changed = updated.data_dir != previous.data_dir.resolve()
        if not server_id_changed and not data_dir_changed:
            return {
                "status": "UNCHANGED",
                "server_id": updated.server_id,
                "data_dir": str(updated.data_dir),
                "restarted": False,
            }

        health = read_health(previous)
        was_running = bool(
            health and health.get("server_id") == previous.server_id
        )
        if was_running and not stop_server_process(previous):
            raise RuntimeError("无法停止当前 Server，配置未保存")

        try:
            save_runtime_config(self.config_path, updated)
            self.config = updated
        except Exception:
            self.config = previous
            if was_running:
                self.start_server()
            raise

        restart_status = "NOT_RUNNING"
        if was_running:
            restart_status = self.start_server()["status"]
        return {
            "status": "RESTARTING" if was_running else "SAVED",
            "server_id": updated.server_id,
            "data_dir": str(updated.data_dir),
            "server_id_changed": server_id_changed,
            "data_dir_changed": data_dir_changed,
            "old_data_dir": str(previous.data_dir),
            "restarted": was_running,
            "restart_status": restart_status,
        }

    def start_server(self) -> dict[str, str]:
        health = read_health(self.config)
        if health and health.get("server_id") == self.config.server_id:
            return {"status": "RUNNING"}
        if health:
            raise RuntimeError(
                f"端口 {self.config.port} 已被另一个 Server 占用"
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
            f"/api/v1/transfers/{transfer_id}/server-receive",
            method="POST",
            token=self.config.admin_token,
        )

    def reject_transfer(self, transfer_id: str) -> dict[str, Any]:
        return api_request(
            self.config,
            f"/api/v1/transfers/{transfer_id}/reject",
            method="POST",
            token=self.config.admin_token,
        )


def run_server_webview(config_path: Path | None = None) -> None:
    index = frontend_index()
    if not index.exists():
        raise FileNotFoundError(f"Vue frontend has not been built: {index}")
    api = ServerDesktopApi(config_path)
    if os.name == "nt":
        configure_bundled_webview2_runtime()
    window = webview.create_window(
        "File Backup Server",
        index.as_uri(),
        js_api=api,
        width=1240,
        height=780,
        min_size=(980, 680),
        background_color="#f3f5f8",
    )
    api._window = window
    if sys.platform == "darwin":
        from server.app.macos_menu_bar import MacOSServerMenuBar

        menu_bar = MacOSServerMenuBar(window, api)
        window.events.closing += menu_bar.handle_window_closing
        webview.start(menu_bar.install, debug=False)
    elif os.name == "nt":
        tray = DesktopTrayController(
            window,
            name="FileBackupServer",
            title="File Backup Server 管理器正在运行",
            icon_path=index.parent / "app-icon.png",
        )
        tray.start()
        try:
            webview.start(debug=False)
        finally:
            tray.stop()
    else:
        webview.start(debug=False)
