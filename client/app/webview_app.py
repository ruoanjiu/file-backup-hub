from __future__ import annotations

import sys
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import webview
import yaml
import httpx

from client.app.backup import run_backup_for_task
from client.app.config import (
    ServerSection,
    default_client_data_dir,
    default_config_path,
    load_config,
)
from client.app.restore import run_restore, run_verify
from client.app.transfer import receive_transfer, reject_transfer, send_transfer
from client.app.uploader import BackupServerClient, list_backups_across_servers


def frontend_index() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "frontend" / "dist" / "index.html"


class ClientDesktopApi:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or default_config_path()
        self.window: Any | None = None

    def _config(self):
        return load_config(self.config_path)

    def _raw(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        return yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}

    def _save_raw(self, data: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temp_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        if os.name != "nt":
            temp_path.chmod(0o600)
        temp_path.replace(self.config_path)

    def _backup_config(self, reason: str = "disconnect") -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_dir = self.config_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{self.config_path.stem}-before-{reason}-{timestamp}.yaml"
        shutil.copy2(self.config_path, backup_path)
        if os.name != "nt":
            backup_path.chmod(0o600)
        return backup_path

    def bootstrap(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"mode": "client", "configured": False}
        config = self._config()
        servers: list[dict[str, Any]] = []
        devices: list[dict[str, Any]] = []
        inbox: list[dict[str, Any]] = []
        for server in config.servers:
            if not server.enabled:
                servers.append(
                    {
                        "id": server.id,
                        "name": server.name,
                        "url": server.base_url,
                        "status": "revoked",
                        "enabled": False,
                        "usage": None,
                        "percent": 0,
                    }
                )
                continue
            client = BackupServerClient(server)
            item = {
                "id": server.id,
                "name": server.name,
                "url": server.base_url,
                "status": "offline",
                "usage": None,
                "percent": 0,
                "enabled": True,
            }
            try:
                health = client.health()
                item["status"] = "ok" if health.get("server_id") == server.id else "mismatch"
                if not devices:
                    devices = client.list_devices().get("items", [])
                inbox.extend(
                    {**transfer, "server_id": server.id}
                    for transfer in client.list_transfer_inbox().get("items", [])
                )
            except Exception as exc:
                item["error"] = str(exc)
            servers.append(item)
        try:
            backups = list_backups_across_servers(
                config,
                server_id="all",
                machine_id=config.client.machine_id,
                limit=200,
            ).get("items", [])
        except Exception:
            backups = []
        tasks = [
            {
                "name": task.name,
                "schedule": (
                    f"每天 {task.schedule_time}" if task.schedule_enabled else "手动"
                ),
                "sources": len(task.roots),
                "last_status": None,
            }
            for task in config.tasks
        ]
        unique_inbox: dict[str, dict[str, Any]] = {}
        for item in inbox:
            unique_inbox.setdefault(str(item["transfer_id"]), item)
        return {
            "mode": "client",
            "configured": True,
            "device": {
                "device_id": config.client.machine_id,
                "display_name": config.client.display_name,
            },
            "servers": servers,
            "devices": devices,
            "tasks": tasks,
            "inbox": list(unique_inbox.values()),
            "backups": backups,
            "inbox_dir": str(config.transfer.inbox_dir),
            "config_path": str(self.config_path),
        }

    def first_setup(self, values: dict[str, str]) -> dict[str, Any]:
        url = values["server_url"].strip().rstrip("/")
        server_id = values["server_id"].strip()
        temporary = ServerSection(
            base_url=url,
            token="PAIRING_PENDING",
            timeout_seconds=15,
            verify_tls=url.lower().startswith("https://"),
            id=server_id,
            name=values.get("server_name") or "Server A",
        )
        paired = BackupServerClient(temporary).pair_device(
            values["pairing_code"].strip(),
            values["device_id"].strip(),
            values["display_name"].strip(),
        )
        data_dir = (
            Path(values["data_dir"]).expanduser()
            if values.get("data_dir", "").strip()
            else default_client_data_dir()
        )
        inbox_dir = (
            values["inbox_dir"].strip()
            if values.get("inbox_dir", "").strip()
            else str(Path.home() / "Downloads" / "FileBackup Inbox")
        )
        config = {
            "client": {
                "machine_id": values["device_id"].strip(),
                "display_name": values["display_name"].strip(),
                "timezone": "Asia/Shanghai",
                "data_dir": str(data_dir),
                "temp_dir": str(data_dir / "tmp"),
                "outbox_dir": str(data_dir / "outbox"),
            },
            "servers": [
                {
                    "id": server_id,
                    "name": values.get("server_name") or "Server A",
                    "base_url": url,
                    "token": paired["token"],
                    "timeout_seconds": 60,
                    "verify_tls": url.lower().startswith("https://"),
                    "enabled": True,
                }
            ],
            "backup": {
                "required_copies": 1,
                "retry_count": 3,
                "retry_interval_seconds": 5,
                "copy_stability_check": True,
                "keep_local_until_all_uploaded": True,
            },
            "restore": {
                "allowed_roots": [],
                "rollback_dir": str(data_dir / "rollback"),
                "require_same_machine_id": True,
            },
            "transfer": {
                "inbox_dir": inbox_dir,
                "temp_dir": str(data_dir / "transfer-tmp"),
                "allowed_send_roots": [],
                "require_confirmation": True,
                "overwrite_existing": False,
            },
            "tasks": [],
        }
        self._save_raw(config)
        return self.bootstrap()

    def choose_files(self) -> list[str]:
        if self.window is None:
            return []
        result = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        return [str(item) for item in (result or [])]

    def choose_folder(self) -> list[str]:
        if self.window is None:
            return []
        result = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        return [str(item) for item in (result or [])]

    def choose_receive_folder(self, directory: str = "") -> list[str]:
        if self.window is None:
            return []
        result = self.window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=directory or "",
        )
        return [str(item) for item in (result or [])]

    def create_task(self, task: dict[str, Any]) -> dict[str, Any]:
        raw = self._raw()
        roots = [
            {
                "path": source,
                "source_type": "auto",
                "recursive": True,
                "include": ["*"],
                "exclude": ["*.tmp", "~$*.xlsx", "__pycache__/*"],
            }
            for source in task.get("sources", [])
        ]
        if not task.get("name") or not roots:
            raise ValueError("任务名称和备份来源不能为空")
        raw.setdefault("tasks", []).append(
            {
                "name": str(task["name"]),
                "enabled": True,
                "schedule_enabled": bool(task.get("scheduled", True)),
                "schedule_time": str(task.get("time") or "04:00"),
                "roots": roots,
            }
        )
        allowed = raw.setdefault("restore", {}).setdefault("allowed_roots", [])
        for source in task.get("sources", []):
            path = Path(source)
            root = str(path if path.is_dir() else path.parent)
            if root not in allowed:
                allowed.append(root)
        self._save_raw(raw)
        self._config()
        return {"status": "SUCCESS"}

    def run_backup(self, task_name: str) -> dict[str, Any]:
        config = self._config()
        return run_backup_for_task(config, config.get_task(task_name)).__dict__

    def send(self, paths: list[str], receiver: str, server_id: str) -> dict[str, Any]:
        return send_transfer(
            self._config(),
            [Path(path) for path in paths],
            receiver,
            server_id=server_id,
        ).__dict__

    def receive(self, transfer_id: str, server_id: str, destination: str) -> dict[str, Any]:
        return receive_transfer(
            self._config(),
            transfer_id,
            server_id=server_id,
            destination=Path(destination) if destination else None,
        ).__dict__

    def reject_transfer(self, transfer_id: str, server_id: str) -> dict[str, Any]:
        return reject_transfer(
            self._config(),
            transfer_id,
            server_id=server_id,
        )

    def verify_backup(self, backup_id: str, server_id: str) -> dict[str, Any]:
        return run_verify(self._config(), backup_id, server_id=server_id).__dict__

    def restore_backup(self, backup_id: str, server_id: str, destination: str) -> dict[str, Any]:
        config = self._config()
        source_server = config.server if server_id == "auto" else config.get_server(server_id)
        manifest = BackupServerClient(source_server).download_manifest(backup_id)
        path_maps = [
            f"{root}={Path(destination) / Path(root).name}"
            for root in manifest.get("roots", [])
        ]
        return run_restore(
            config,
            backup_id,
            server_id=server_id,
            path_maps=path_maps,
        ).__dict__

    def pair(self, server_id: str, code: str, display_name: str) -> dict[str, Any]:
        config = self._config()
        server = next((item for item in config.servers if item.id == server_id), None)
        if server is None:
            raise ValueError(f"Server not found: {server_id}")
        response = BackupServerClient(server).pair_device(
            code,
            config.client.machine_id,
            display_name,
        )
        raw = self._raw()
        raw.setdefault("client", {})["display_name"] = display_name
        for item in raw.get("servers", []):
            if item.get("id") == server_id:
                item["token"] = response["token"]
                item["enabled"] = True
        backup = raw.setdefault("backup", {})
        if int(backup.get("required_copies", 0)) < 1:
            backup["required_copies"] = 1
        self._save_raw(raw)
        return {key: value for key, value in response.items() if key != "token"}

    def disconnect_server(self, server_id: str) -> dict[str, Any]:
        config = self._config()
        server = config.get_server(server_id)
        backup_path = self._backup_config("disconnect")
        remote_revoked = False
        try:
            response = BackupServerClient(server).revoke_self()
            if response.get("status") != "REVOKED":
                raise RuntimeError("Server did not confirm device revocation")
            remote_revoked = True
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 401:
                raise
            remote_revoked = True
        raw = self._raw()
        found = False
        for item in raw.get("servers", []):
            if item.get("id") == server_id:
                item["token"] = ""
                item["enabled"] = False
                found = True
                break
        if not found:
            raise ValueError(f"Server not found in local config: {server_id}")
        remaining = sum(bool(item.get("enabled", True)) for item in raw.get("servers", []))
        backup = raw.setdefault("backup", {})
        required = int(backup.get("required_copies", remaining))
        backup["required_copies"] = min(required, remaining)
        self._save_raw(raw)
        self._config()
        return {
            "status": "DISCONNECTED",
            "server_id": server_id,
            "remote_revoked": remote_revoked,
            "backups_deleted": False,
            "remaining_servers": remaining,
            "required_copies": backup["required_copies"],
            "config_backup": str(backup_path),
        }

    def delete_server(self, server_id: str) -> dict[str, Any]:
        raw = self._raw()
        servers = raw.get("servers", [])
        target = next((item for item in servers if item.get("id") == server_id), None)
        if target is None:
            raise ValueError(f"Server not found in local config: {server_id}")
        if bool(target.get("enabled", True)) or str(target.get("token") or "").strip():
            raise ValueError("必须先退出 Server 并清空 Token，才能删除本地配置")
        backup_path = self._backup_config("delete-server")
        raw["servers"] = [item for item in servers if item.get("id") != server_id]
        remaining = sum(bool(item.get("enabled", True)) for item in raw["servers"])
        backup = raw.setdefault("backup", {})
        current_required = int(backup.get("required_copies", remaining))
        backup["required_copies"] = min(current_required, remaining) if remaining else 0
        self._save_raw(raw)
        self._config()
        return {
            "status": "DELETED_LOCAL_CONFIG",
            "server_id": server_id,
            "remote_data_deleted": False,
            "local_backup_data_deleted": False,
            "remaining_servers": len(raw["servers"]),
            "config_backup": str(backup_path),
        }

    def add_server(self, values: dict[str, str]) -> dict[str, Any]:
        raw = self._raw()
        server_id = values["server_id"].strip()
        if not server_id:
            raise ValueError("Server ID 不能为空")
        if any(item.get("id") == server_id for item in raw.get("servers", [])):
            raise ValueError("该 Server ID 已存在；请使用重新配对")
        url = values["server_url"].strip().rstrip("/")
        temporary = ServerSection(
            base_url=url,
            token="PAIRING_PENDING",
            timeout_seconds=15,
            verify_tls=url.lower().startswith("https://"),
            id=server_id,
            name=values.get("server_name") or server_id,
        )
        config = self._config()
        paired = BackupServerClient(temporary).pair_device(
            values["pairing_code"].strip(),
            config.client.machine_id,
            values.get("display_name", "").strip() or config.client.display_name,
        )
        self._backup_config("add-server")
        raw.setdefault("servers", []).append(
            {
                "id": server_id,
                "name": values.get("server_name") or server_id,
                "base_url": url,
                "token": paired["token"],
                "timeout_seconds": 60,
                "verify_tls": url.lower().startswith("https://"),
                "enabled": True,
            }
        )
        backup = raw.setdefault("backup", {})
        if int(backup.get("required_copies", 0)) < 1:
            backup["required_copies"] = 1
        self._save_raw(raw)
        self._config()
        return {key: value for key, value in paired.items() if key != "token"}

    def rename_device(self, device_id: str, display_name: str) -> dict[str, Any]:
        config = self._config()
        if device_id != config.client.machine_id:
            raise ValueError("Client 只能修改本机名称")
        response = BackupServerClient(config.server).rename_device(device_id, display_name)
        raw = self._raw()
        raw.setdefault("client", {})["display_name"] = display_name
        self._save_raw(raw)
        return response

    def save_inbox(self, inbox_dir: str) -> dict[str, str]:
        raw = self._raw()
        raw.setdefault("transfer", {})["inbox_dir"] = inbox_dir
        self._save_raw(raw)
        self._config()
        return {"status": "SUCCESS"}


def run_client_webview(config_path: Path | None = None) -> None:
    index = frontend_index()
    if not index.exists():
        raise FileNotFoundError(f"Vue frontend has not been built: {index}")
    api = ClientDesktopApi(config_path)
    window = webview.create_window(
        "File Backup",
        index.as_uri(),
        js_api=api,
        width=1480,
        height=920,
        min_size=(1080, 720),
        background_color="#f3f5f8",
    )
    api.window = window
    if sys.platform == "darwin":
        from client.app.macos_menu_bar import MacOSClientMenuBar

        menu_bar = MacOSClientMenuBar(window, api)
        window.events.closing += menu_bar.handle_window_closing
        webview.start(menu_bar.install, debug=False)
    else:
        webview.start(debug=False)
