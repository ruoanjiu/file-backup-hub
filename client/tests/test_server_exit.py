from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from client.app.config import ServerSection, load_config
from client.app.uploader import BackupServerClient
from client.app.webview_app import ClientDesktopApi
from client.app import webview_app


@pytest.fixture(autouse=True)
def disable_embedded_agent_process_management(monkeypatch) -> None:
    monkeypatch.setattr(
        ClientDesktopApi,
        "_sync_agent_after_config_change",
        lambda self: {"status": "STOPPED", "running": False},
    )


def write_config(path: Path, servers: list[dict]) -> Path:
    data_dir = path.parent / "client-data"
    config = {
        "client": {
            "machine_id": "office-pc",
            "display_name": "办公室电脑",
            "timezone": "Asia/Shanghai",
            "data_dir": str(data_dir),
            "temp_dir": str(data_dir / "tmp"),
            "outbox_dir": str(data_dir / "outbox"),
        },
        "servers": servers,
        "backup": {
            "required_copies": sum(bool(item.get("enabled", True)) for item in servers),
            "retry_count": 1,
            "retry_interval_seconds": 0,
        },
        "restore": {
            "allowed_roots": [],
            "rollback_dir": str(data_dir / "rollback"),
        },
        "transfer": {
            "inbox_dir": str(data_dir / "inbox"),
            "temp_dir": str(data_dir / "transfer-tmp"),
            "allowed_send_roots": [],
        },
        "tasks": [
            {
                "name": "daily_logs",
                "enabled": True,
                "schedule_enabled": False,
                "schedule_time": "04:00",
                "roots": [
                    {
                        "path": str(path.parent / "source"),
                        "recursive": True,
                        "include": ["*"],
                        "exclude": [],
                    }
                ],
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def server_item(server_id: str, token: str, *, enabled: bool = True) -> dict:
    return {
        "id": server_id,
        "name": server_id.upper(),
        "base_url": f"https://{server_id}.example.test",
        "token": token,
        "timeout_seconds": 10,
        "verify_tls": True,
        "enabled": enabled,
    }


def test_uploader_calls_self_revoke_endpoint_without_exposing_token() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("Authorization", "")
        return httpx.Response(
            200,
            json={"device_id": "office-pc", "status": "REVOKED", "enabled": False},
        )

    server = ServerSection(
        base_url="https://server-a.example.test",
        token="dummy-token-a",
        id="server-a",
        name="Server A",
    )
    result = BackupServerClient(server, transport=httpx.MockTransport(handler)).revoke_self()

    assert result["status"] == "REVOKED"
    assert seen == {
        "method": "POST",
        "path": "/api/v1/devices/self/revoke",
        "authorization": "Bearer dummy-token-a",
    }


def test_leave_one_server_revokes_remote_and_disables_only_selected_server(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = write_config(
        tmp_path / "config.yaml",
        [
            server_item("server-a", "dummy-token-a"),
            server_item("server-b", "dummy-token-b"),
        ],
    )
    local_backup = tmp_path / "client-data" / "outbox" / "history.bundle"
    local_backup.parent.mkdir(parents=True, exist_ok=True)
    local_backup.write_bytes(b"keep-local-backup")
    calls: list[str] = []

    class FakeServerClient:
        def __init__(self, server) -> None:
            self.server = server

        def revoke_self(self):
            calls.append(self.server.id)
            return {"device_id": "office-pc", "status": "REVOKED", "enabled": False}

    monkeypatch.setattr(webview_app, "BackupServerClient", FakeServerClient)
    api = ClientDesktopApi(config_path)

    result = api.leave_server("server-a")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    servers = {item["id"]: item for item in raw["servers"]}

    assert result == {
        "status": "LEFT",
        "server_id": "server-a",
        "server_name": "SERVER-A",
        "enabled": False,
        "remote_revoked": True,
    }
    assert calls == ["server-a"]
    assert servers["server-a"]["enabled"] is False
    assert servers["server-a"]["token"] == ""
    assert servers["server-b"]["enabled"] is True
    assert servers["server-b"]["token"] == "dummy-token-b"
    assert [item["name"] for item in raw["tasks"]] == ["daily_logs"]
    assert raw["backup"]["required_copies"] == 1
    assert local_backup.read_bytes() == b"keep-local-backup"
    assert [item.id for item in load_config(config_path).enabled_servers()] == ["server-b"]

    deleted = api.delete_server("server-a")
    after_delete = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert deleted["status"] == "DELETED_LOCAL"
    assert [item["id"] for item in after_delete["servers"]] == ["server-b"]
    assert [item["name"] for item in after_delete["tasks"]] == ["daily_logs"]
    assert local_backup.read_bytes() == b"keep-local-backup"


def test_leave_last_server_keeps_disabled_config_for_repairing(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = write_config(
        tmp_path / "config.yaml",
        [server_item("server-a", "dummy-token-a")],
    )

    class FakeServerClient:
        def __init__(self, server) -> None:
            self.server = server

        def revoke_self(self):
            return {"device_id": "office-pc", "status": "REVOKED", "enabled": False}

    monkeypatch.setattr(webview_app, "BackupServerClient", FakeServerClient)
    api = ClientDesktopApi(config_path)
    api.leave_server("server-a")

    deleted = api.delete_server("server-a")

    config = load_config(config_path)
    assert deleted["remaining_servers"] == 0
    assert config.servers == []
    assert config.enabled_servers() == []
    assert config.backup.required_copies == 0
    assert [task.name for task in config.tasks] == ["daily_logs"]
    state = api.bootstrap()
    assert state["servers"] == []


def test_active_server_must_be_exited_before_local_delete(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "config.yaml",
        [server_item("server-a", "dummy-token-a")],
    )
    api = ClientDesktopApi(config_path)

    with pytest.raises(RuntimeError, match="请先退出此 Server"):
        api.delete_server("server-a")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert [item["id"] for item in raw["servers"]] == ["server-a"]
    assert raw["servers"][0]["token"] == "dummy-token-a"


def test_leave_server_failure_preserves_local_token(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = write_config(
        tmp_path / "config.yaml",
        [server_item("server-a", "dummy-token-a")],
    )

    class OfflineServerClient:
        def __init__(self, server) -> None:
            self.server = server

        def revoke_self(self):
            request = httpx.Request("POST", self.server.base_url)
            raise httpx.ConnectError("offline", request=request)

    monkeypatch.setattr(webview_app, "BackupServerClient", OfflineServerClient)
    api = ClientDesktopApi(config_path)

    with pytest.raises(RuntimeError, match="本地配置未修改"):
        api.leave_server("server-a")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["servers"][0]["enabled"] is True
    assert raw["servers"][0]["token"] == "dummy-token-a"


@pytest.mark.parametrize("status_code", [401, 403])
def test_invalid_bearer_allows_confirmed_local_only_exit(
    monkeypatch, tmp_path: Path, status_code: int
) -> None:
    config_path = write_config(
        tmp_path / "config.yaml",
        [server_item("server-a", "expired-dummy-token")],
    )
    local_backup = tmp_path / "client-data" / "outbox" / "history.bundle"
    local_backup.parent.mkdir(parents=True, exist_ok=True)
    local_backup.write_bytes(b"keep-local-backup")

    class InvalidTokenServerClient:
        def __init__(self, server) -> None:
            self.server = server

        def revoke_self(self):
            request = httpx.Request(
                "POST",
                f"{self.server.base_url}/api/v1/devices/self/revoke",
            )
            response = httpx.Response(
                status_code,
                request=request,
                json={"detail": "Invalid bearer token"},
            )
            raise httpx.HTTPStatusError(
                "invalid token",
                request=request,
                response=response,
            )

    monkeypatch.setattr(webview_app, "BackupServerClient", InvalidTokenServerClient)
    api = ClientDesktopApi(config_path)

    first_result = api.leave_server("server-a")
    before_fallback = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert first_result["status"] == "AUTH_INVALID"
    assert first_result["local_only_available"] is True
    assert before_fallback["servers"][0]["enabled"] is True
    assert before_fallback["servers"][0]["token"] == "expired-dummy-token"

    fallback = api.leave_server_local("server-a")
    after_fallback = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert fallback == {
        "status": "LEFT_LOCAL_ONLY",
        "server_id": "server-a",
        "server_name": "SERVER-A",
        "enabled": False,
        "remote_revoked": False,
    }
    assert after_fallback["servers"][0]["enabled"] is False
    assert after_fallback["servers"][0]["token"] == ""
    assert [item["name"] for item in after_fallback["tasks"]] == ["daily_logs"]
    assert local_backup.read_bytes() == b"keep-local-backup"


def test_disabled_server_can_be_repaired_and_receives_new_token(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = write_config(
        tmp_path / "config.yaml",
        [server_item("server-a", "", enabled=False)],
    )

    class PairingServerClient:
        def __init__(self, server) -> None:
            self.server = server

        def pair_device(self, code, device_id, display_name):
            assert self.server.enabled is True
            assert self.server.token == "PAIRING_PENDING"
            return {
                "device_id": device_id,
                "display_name": display_name,
                "token": "new-dummy-token",
                "server_id": self.server.id,
            }

    monkeypatch.setattr(webview_app, "BackupServerClient", PairingServerClient)
    api = ClientDesktopApi(config_path)

    result = api.pair("server-a", "683291", "办公室电脑")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert result == {
        "device_id": "office-pc",
        "display_name": "办公室电脑",
        "server_id": "server-a",
    }
    assert raw["servers"][0]["enabled"] is True
    assert raw["servers"][0]["token"] == "new-dummy-token"
    assert raw["backup"]["required_copies"] == 1


def test_server_can_be_added_after_last_local_config_is_deleted(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = write_config(tmp_path / "config.yaml", [])

    class AddingServerClient:
        def __init__(self, server) -> None:
            self.server = server

        def pair_device(self, code, device_id, display_name):
            assert code == "683291"
            assert device_id == "office-pc"
            return {
                "device_id": device_id,
                "display_name": display_name,
                "token": "new-server-token",
                "server_id": self.server.id,
            }

    monkeypatch.setattr(webview_app, "BackupServerClient", AddingServerClient)
    api = ClientDesktopApi(config_path)

    result = api.add_server(
        {
            "server_url": "https://server-new.example.test",
            "server_id": "server-new",
            "server_name": "Server New",
            "pairing_code": "683291",
            "display_name": "办公室电脑",
        }
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert result["server_id"] == "server-new"
    assert raw["servers"][0]["id"] == "server-new"
    assert raw["servers"][0]["enabled"] is True
    assert raw["servers"][0]["token"] == "new-server-token"
    assert raw["backup"]["required_copies"] == 1
    assert [item["name"] for item in raw["tasks"]] == ["daily_logs"]
