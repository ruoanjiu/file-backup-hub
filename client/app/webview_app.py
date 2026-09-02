from __future__ import annotations

import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import httpx
import webview
import yaml

from desktop_tray import DesktopTrayController
from client.app.agent_runtime import (
    agent_status,
    restart_agent_process,
    start_agent_process,
    stop_agent_process,
)
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
from client.app.webview_runtime import configure_bundled_webview2_runtime


def frontend_index() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "frontend" / "dist" / "index.html"


def to_bridge_data(value: Any) -> Any:
    """Convert desktop API results to values pywebview can encode as JSON."""
    if is_dataclass(value) and not isinstance(value, type):
        return to_bridge_data(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return to_bridge_data(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): to_bridge_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_bridge_data(item) for item in value]
    return value


class ClientDesktopApi:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or default_config_path()
        self._window: Any | None = None

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
        temp_path.replace(self.config_path)

    @staticmethod
    def _server_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
        servers = raw.get("servers")
        if isinstance(servers, list):
            items = [item for item in servers if isinstance(item, dict)]
            for index, item in enumerate(items):
                item.setdefault("id", f"server-{index + 1}")
            return items
        legacy = raw.pop("server", None)
        if isinstance(legacy, dict):
            legacy.setdefault("id", "server-1")
            raw["servers"] = [legacy]
            return raw["servers"]
        return []

    @staticmethod
    def _request_error(action: str, exc: Exception) -> RuntimeError:
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                detail = exc.response.json().get("detail")
            except Exception:
                detail = None
            return RuntimeError(
                f"{action}失败：{detail or f'Server 返回 HTTP {exc.response.status_code}'}"
            )
        if isinstance(exc, httpx.RequestError):
            return RuntimeError(f"{action}失败：无法连接 Server，本地配置未修改")
        return RuntimeError(f"{action}失败：{exc}")

    def bootstrap(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {
                "mode": "client",
                "configured": False,
                "agent": agent_status(self.config_path),
            }
        config = self._config()
        servers: list[dict[str, Any]] = []
        devices: list[dict[str, Any]] = []
        inbox: list[dict[str, Any]] = []
        for server in config.servers:
            item = {
                "id": server.id,
                "name": server.name,
                "url": server.base_url,
                "enabled": server.enabled,
                "status": "offline" if server.enabled else "disabled",
                "usage": None,
                "percent": 0,
            }
            if not server.enabled:
                servers.append(item)
                continue
            client = BackupServerClient(server)
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
            "agent": agent_status(self.config_path),
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
        self.start_agent()
        return self.bootstrap()

    def choose_files(self) -> list[str]:
        if self._window is None:
            return []
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True
        )
        return [str(item) for item in (result or [])]

    def choose_folder(self) -> list[str]:
        if self._window is None:
            return []
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return [str(item) for item in (result or [])]

    def choose_receive_folder(self, directory: str = "") -> list[str]:
        if self._window is None:
            return []
        result = self._window.create_file_dialog(
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
        self._sync_agent_after_config_change()
        return {"status": "SUCCESS"}

    def run_backup(self, task_name: str) -> dict[str, Any]:
        config = self._config()
        return to_bridge_data(
            run_backup_for_task(config, config.get_task(task_name))
        )

    def send(self, paths: list[str], receiver: str, server_id: str) -> dict[str, Any]:
        return to_bridge_data(
            send_transfer(
                self._config(),
                [Path(path) for path in paths],
                receiver,
                server_id=server_id,
            )
        )

    def receive(self, transfer_id: str, server_id: str, destination: str) -> dict[str, Any]:
        return to_bridge_data(
            receive_transfer(
                self._config(),
                transfer_id,
                server_id=server_id,
                destination=Path(destination) if destination else None,
            )
        )

    def reject_transfer(self, transfer_id: str, server_id: str) -> dict[str, Any]:
        return to_bridge_data(
            reject_transfer(
                self._config(),
                transfer_id,
                server_id=server_id,
            )
        )

    def verify_backup(self, backup_id: str, server_id: str) -> dict[str, Any]:
        return to_bridge_data(
            run_verify(self._config(), backup_id, server_id=server_id)
        )

    def restore_backup(self, backup_id: str, server_id: str, destination: str) -> dict[str, Any]:
        config = self._config()
        source_server = config.server if server_id == "auto" else config.get_server(server_id)
        manifest = BackupServerClient(source_server).download_manifest(backup_id)
        path_maps = [
            f"{root}={Path(destination) / Path(root).name}"
            for root in manifest.get("roots", [])
        ]
        return to_bridge_data(
            run_restore(
                config,
                backup_id,
                server_id=server_id,
                path_maps=path_maps,
            )
        )

    def pair(self, server_id: str, code: str, display_name: str) -> dict[str, Any]:
        config = self._config()
        server = next(
            (item for item in config.servers if item.id == server_id),
            None,
        )
        if server is None:
            raise ValueError(f"未找到 Server 配置：{server_id}")
        pairing_server = ServerSection(
            base_url=server.base_url,
            token="PAIRING_PENDING",
            timeout_seconds=server.timeout_seconds,
            verify_tls=server.verify_tls,
            id=server.id,
            name=server.name,
            enabled=True,
        )
        try:
            response = BackupServerClient(pairing_server).pair_device(
                code,
                config.client.machine_id,
                display_name,
            )
        except Exception as exc:
            raise self._request_error("重新配对" if not server.enabled else "配对", exc) from exc
        raw = self._raw()
        raw.setdefault("client", {})["display_name"] = display_name
        server_items = self._server_items(raw)
        for item in server_items:
            if item.get("id") == server_id:
                item["token"] = response["token"]
                item["enabled"] = True
        enabled_count = sum(bool(item.get("enabled", True)) for item in server_items)
        if enabled_count:
            current_required = int(raw.setdefault("backup", {}).get("required_copies", 1))
            raw["backup"]["required_copies"] = max(
                1,
                min(current_required or 1, enabled_count),
            )
        self._save_raw(raw)
        self._sync_agent_after_config_change()
        return {key: value for key, value in response.items() if key != "token"}

    def leave_server(self, server_id: str) -> dict[str, Any]:
        config = self._config()
        server = next(
            (item for item in config.servers if item.id == server_id),
            None,
        )
        if server is None:
            raise ValueError(f"未找到 Server 配置：{server_id}")
        if not server.enabled:
            return {
                "status": "ALREADY_LEFT",
                "server_id": server.id,
                "server_name": server.name,
                "enabled": False,
            }
        try:
            response = BackupServerClient(server).revoke_self()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                return {
                    "status": "AUTH_INVALID",
                    "server_id": server.id,
                    "server_name": server.name,
                    "enabled": True,
                    "local_only_available": True,
                }
            raise self._request_error("退出此 Server", exc) from exc
        except Exception as exc:
            raise self._request_error("退出此 Server", exc) from exc
        if response.get("status") != "REVOKED":
            raise RuntimeError("退出此 Server失败：Server 未确认撤销授权，本地配置未修改")

        return self._disable_server_locally(
            server_id,
            status="LEFT",
            remote_revoked=True,
        )

    def leave_server_local(self, server_id: str) -> dict[str, Any]:
        return self._disable_server_locally(
            server_id,
            status="LEFT_LOCAL_ONLY",
            remote_revoked=False,
        )

    def _disable_server_locally(
        self,
        server_id: str,
        *,
        status: str,
        remote_revoked: bool,
    ) -> dict[str, Any]:
        raw = self._raw()
        server_items = self._server_items(raw)
        matched = False
        server_name = server_id
        for item in server_items:
            if item.get("id") == server_id:
                server_name = str(item.get("name") or server_id)
                if item.get("enabled") is False and not str(item.get("token") or "").strip():
                    return {
                        "status": "ALREADY_LEFT",
                        "server_id": server_id,
                        "server_name": server_name,
                        "enabled": False,
                        "remote_revoked": remote_revoked,
                    }
                item["token"] = ""
                item["enabled"] = False
                matched = True
        if not matched:
            raise RuntimeError("本地未找到对应 Server 配置")
        enabled_count = sum(bool(item.get("enabled", True)) for item in server_items)
        backup_raw = raw.setdefault("backup", {})
        current_required = int(backup_raw.get("required_copies", enabled_count))
        backup_raw["required_copies"] = (
            min(max(current_required, 1), enabled_count)
            if enabled_count
            else 0
        )
        try:
            self._save_raw(raw)
        except Exception as exc:
            message = (
                "Server 已撤销授权，但本地配置保存失败；请勿继续使用旧 Token"
                if remote_revoked
                else "本地退出失败：配置保存失败"
            )
            raise RuntimeError(message) from exc
        self._sync_agent_after_config_change()
        return {
            "status": status,
            "server_id": server_id,
            "server_name": server_name,
            "enabled": False,
            "remote_revoked": remote_revoked,
        }

    def delete_server(self, server_id: str) -> dict[str, Any]:
        raw = self._raw()
        server_items = self._server_items(raw)
        target = next(
            (item for item in server_items if item.get("id") == server_id),
            None,
        )
        if target is None:
            raise ValueError(f"未找到 Server 配置：{server_id}")
        if bool(target.get("enabled", True)) or str(target.get("token") or "").strip():
            raise RuntimeError("请先退出此 Server，确认 Token 已清空后再删除")

        raw["servers"] = [
            item for item in server_items if item.get("id") != server_id
        ]
        enabled_count = sum(
            bool(item.get("enabled", True)) for item in raw["servers"]
        )
        backup_raw = raw.setdefault("backup", {})
        current_required = int(backup_raw.get("required_copies", enabled_count))
        backup_raw["required_copies"] = (
            min(max(current_required, 1), enabled_count)
            if enabled_count
            else 0
        )
        self._save_raw(raw)
        self._sync_agent_after_config_change()
        return {
            "status": "DELETED_LOCAL",
            "server_id": server_id,
            "server_name": str(target.get("name") or server_id),
            "remaining_servers": len(raw["servers"]),
        }

    def add_server(self, values: dict[str, str]) -> dict[str, Any]:
        raw = self._raw()
        server_items = self._server_items(raw)
        server_id = values.get("server_id", "").strip()
        server_url = values.get("server_url", "").strip().rstrip("/")
        display_name = values.get("display_name", "").strip()
        if not server_id or not server_url:
            raise ValueError("Server ID 和 Server URL 不能为空")
        if any(item.get("id") == server_id for item in server_items):
            raise ValueError("该 Server ID 已存在；已退出的 Server 请使用重新配对")
        temporary = ServerSection(
            base_url=server_url,
            token="PAIRING_PENDING",
            timeout_seconds=15,
            verify_tls=server_url.lower().startswith("https://"),
            id=server_id,
            name=values.get("server_name") or server_id,
            enabled=True,
        )
        try:
            response = BackupServerClient(temporary).pair_device(
                values.get("pairing_code", "").strip(),
                str(raw.get("client", {}).get("machine_id") or "").strip(),
                display_name,
            )
        except Exception as exc:
            raise self._request_error("添加 Server", exc) from exc
        server_items.append(
            {
                "id": server_id,
                "name": values.get("server_name") or server_id,
                "base_url": server_url,
                "token": response["token"],
                "timeout_seconds": 60,
                "verify_tls": server_url.lower().startswith("https://"),
                "enabled": True,
            }
        )
        raw["servers"] = server_items
        raw.setdefault("client", {})["display_name"] = display_name
        enabled_count = sum(bool(item.get("enabled", True)) for item in server_items)
        backup_raw = raw.setdefault("backup", {})
        current_required = int(backup_raw.get("required_copies", 0))
        backup_raw["required_copies"] = max(
            1,
            min(current_required or 1, enabled_count),
        )
        self._save_raw(raw)
        self._sync_agent_after_config_change()
        return {
            key: value for key, value in response.items() if key != "token"
        }

    def get_agent_status(self) -> dict[str, Any]:
        return agent_status(self.config_path)

    def start_agent(self) -> dict[str, Any]:
        return start_agent_process(self.config_path)

    def stop_agent(self) -> dict[str, Any]:
        return stop_agent_process(self.config_path)

    def restart_agent(self) -> dict[str, Any]:
        return restart_agent_process(self.config_path)

    def _sync_agent_after_config_change(self) -> dict[str, Any]:
        config = self._config()
        if not config.enabled_servers():
            return self.stop_agent()
        return self.restart_agent()

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
    try:
        api.start_agent()
    except Exception:
        pass
    configure_bundled_webview2_runtime()
    window = webview.create_window(
        "File Backup",
        index.as_uri(),
        js_api=api,
        width=1480,
        height=920,
        min_size=(1080, 720),
        background_color="#f3f5f8",
    )
    api._window = window
    tray = DesktopTrayController(
        window,
        name="FileBackupClient",
        title="File Backup Client 正在运行",
        icon_path=index.parent / "app-icon.png",
        on_exit=api.stop_agent,
    )
    tray.start()
    try:
        webview.start(debug=False)
    finally:
        tray.stop()
