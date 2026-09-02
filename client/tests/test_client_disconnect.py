from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from client.app.config import load_config
from client.app.webview_app import ClientDesktopApi
from client.tests.test_client_backup import write_dual_server_config


class FakeServerClient:
    revoked_server_ids: list[str] = []

    def __init__(self, server) -> None:
        self.server = server

    def revoke_self(self) -> dict:
        self.revoked_server_ids.append(self.server.id)
        return {"status": "REVOKED", "backups_deleted": False}

    def pair_device(self, code: str, device_id: str, display_name: str) -> dict:
        assert len(code) == 6
        return {
            "device_id": device_id,
            "display_name": display_name,
            "token": f"fb1:{device_id}:replacement-token",
            "server_id": self.server.id,
        }


def test_client_disconnects_one_server_without_touching_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source_file = source / "keep.log"
    source_file.write_text("must stay", encoding="utf-8")
    config_path = write_dual_server_config(tmp_path, source)
    api = ClientDesktopApi(config_path)
    FakeServerClient.revoked_server_ids.clear()
    monkeypatch.setattr("client.app.webview_app.BackupServerClient", FakeServerClient)

    result = api.disconnect_server("server-a")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    server_a = next(item for item in raw["servers"] if item["id"] == "server-a")
    config = load_config(config_path)

    assert result["status"] == "DISCONNECTED"
    assert result["backups_deleted"] is False
    assert result["remaining_servers"] == 1
    assert FakeServerClient.revoked_server_ids == ["server-a"]
    assert server_a["enabled"] is False
    assert server_a["token"] == ""
    assert raw["backup"]["required_copies"] == 1
    assert [item.id for item in config.enabled_servers()] == ["server-b"]
    assert source_file.read_text(encoding="utf-8") == "must stay"
    assert Path(result["config_backup"]).exists()


def test_client_can_disconnect_and_delete_last_server_without_deleting_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    config_path = write_dual_server_config(tmp_path, source)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    server_b = next(item for item in raw["servers"] if item["id"] == "server-b")
    server_b["enabled"] = False
    server_b["token"] = ""
    raw["backup"]["required_copies"] = 1
    config_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    api = ClientDesktopApi(config_path)
    monkeypatch.setattr("client.app.webview_app.BackupServerClient", FakeServerClient)

    disconnected = api.disconnect_server("server-a")
    assert disconnected["remaining_servers"] == 0
    assert load_config(config_path).enabled_servers() == []
    deleted = api.delete_server("server-a")
    deleted_second = api.delete_server("server-b")
    config = load_config(config_path)
    assert deleted["remote_data_deleted"] is False
    assert deleted["local_backup_data_deleted"] is False
    assert deleted_second["remote_data_deleted"] is False
    assert config.servers == []
    assert config.backup.required_copies == 0


def test_disabled_server_can_be_repaired(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    config_path = write_dual_server_config(tmp_path, source)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    server_a = next(item for item in raw["servers"] if item["id"] == "server-a")
    server_a["enabled"] = False
    server_a["token"] = ""
    raw["backup"]["required_copies"] = 1
    config_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    api = ClientDesktopApi(config_path)
    monkeypatch.setattr("client.app.webview_app.BackupServerClient", FakeServerClient)

    result = api.pair("server-a", "123456", "重新配对设备")
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    repaired = next(item for item in saved["servers"] if item["id"] == "server-a")

    assert result["server_id"] == "server-a"
    assert repaired["enabled"] is True
    assert repaired["token"].startswith("fb1:")


def test_add_server_after_all_local_server_configs_are_deleted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    config_path = write_dual_server_config(tmp_path, source)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["servers"] = []
    raw["backup"]["required_copies"] = 0
    config_path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    api = ClientDesktopApi(config_path)
    monkeypatch.setattr("client.app.webview_app.BackupServerClient", FakeServerClient)

    result = api.add_server(
        {
            "server_url": "http://192.168.1.100:8000",
            "server_id": "server-mac",
            "server_name": "Mac Server",
            "pairing_code": "123456",
            "display_name": "测试设备",
        }
    )
    config = load_config(config_path)

    assert result["server_id"] == "server-mac"
    assert [item.id for item in config.enabled_servers()] == ["server-mac"]
    assert config.backup.required_copies == 1
