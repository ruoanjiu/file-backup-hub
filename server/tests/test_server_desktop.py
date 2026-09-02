from __future__ import annotations

from pathlib import Path

import pytest

from server.app.runtime import (
    ServerRuntimeConfig,
    load_runtime_config,
    new_default_config,
    save_runtime_config,
)
from server.app.webview_manager import (
    ServerDesktopApi,
    resolve_storage_directory,
    storage_directories,
    validate_server_data_dir,
    validate_server_id,
)


def make_config(tmp_path: Path) -> ServerRuntimeConfig:
    return ServerRuntimeConfig(
        server_id="server-a",
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path,
        admin_token="admin",
        client_tokens="",
    )


def test_default_server_has_no_phantom_compat_client() -> None:
    assert new_default_config().client_tokens == ""


def test_only_managed_storage_directories_can_be_opened(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    directories = storage_directories(config)

    assert resolve_storage_directory(config, str(directories["storage"])) == directories[
        "storage"
    ].resolve()

    with pytest.raises(ValueError):
        resolve_storage_directory(config, str(tmp_path.parent))


def test_server_settings_validation(tmp_path: Path) -> None:
    assert validate_server_id("server-a_1") == "server-a_1"
    with pytest.raises(ValueError):
        validate_server_id("server a")
    with pytest.raises(ValueError):
        validate_server_data_dir("relative/path")
    with pytest.raises(ValueError):
        validate_server_data_dir(str(Path(tmp_path.anchor)))


def test_choose_server_data_dir_uses_desktop_dialog(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "server.json"
    save_runtime_config(config_path, make_config(tmp_path / "old-data"))
    api = ServerDesktopApi(config_path)

    class Window:
        def create_file_dialog(self, *_args, **_kwargs):
            return [tmp_path / "chosen-data"]

    api.window = Window()
    assert api.choose_server_data_dir(str(tmp_path)) == [str(tmp_path / "chosen-data")]


def test_save_server_settings_offline_is_atomic_and_keeps_old_data(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config" / "server.json"
    old_data = tmp_path / "old-data"
    new_data = tmp_path / "new-data"
    old_data.mkdir()
    save_runtime_config(config_path, make_config(old_data))
    api = ServerDesktopApi(config_path)
    monkeypatch.setattr("server.app.webview_manager.read_health", lambda *_args, **_kwargs: None)

    result = api.save_server_settings("server-b", str(new_data))
    saved = load_runtime_config(config_path)

    assert result["status"] == "UPDATED"
    assert result["restarted"] is False
    assert saved.server_id == "server-b"
    assert saved.data_dir == new_data
    assert old_data.exists()
    assert Path(result["config_backup"]).exists()


def test_unchanged_server_settings_do_not_restart_or_create_backup(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config" / "server.json"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    save_runtime_config(config_path, make_config(data_dir))
    api = ServerDesktopApi(config_path)

    result = api.save_server_settings("server-a", str(data_dir))

    assert result["status"] == "UNCHANGED"
    assert not (config_path.parent / "backups").exists()
