from __future__ import annotations

import sys
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from run_server_app import ensure_headless_stdio, report_gui_startup_error
from server.app import runtime
from server.app import webview_runtime
from server.app import webview_manager


def test_new_server_config_requires_pairing_instead_of_legacy_token() -> None:
    config = runtime.new_default_config()

    assert config.client_tokens == ""


def test_headless_frozen_mode_creates_writable_log_stream(tmp_path) -> None:
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_handle = None
    try:
        sys.stdout = None
        sys.stderr = None
        log_handle = ensure_headless_stdio(tmp_path)

        assert log_handle is not None
        assert sys.stdout is log_handle
        assert sys.stderr is log_handle
        log_handle.write("headless-log-probe\n")
        log_handle.flush()
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        if log_handle is not None:
            log_handle.close()

    assert (tmp_path / "logs" / "server.log").read_text(
        encoding="utf-8"
    ) == "headless-log-probe\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree semantics")
def test_windows_server_stop_force_terminates_process_tree(
    monkeypatch, tmp_path
) -> None:
    config = runtime.ServerRuntimeConfig(
        server_id="server-test",
        host="127.0.0.1",
        port=8000,
        data_dir=Path(tmp_path),
        admin_token="admin-token",
        client_tokens="client:token",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(
        runtime, "read_health", lambda value: {"server_id": value.server_id}
    )
    monkeypatch.setattr(runtime, "read_pid", lambda value: 4321)
    monkeypatch.setattr(
        runtime.subprocess,
        "run",
        lambda command, **kwargs: (
            calls.append(command) or SimpleNamespace(returncode=0)
        ),
    )

    assert runtime.stop_server_process(config) is True
    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]


def test_bundled_webview2_runtime_is_selected(monkeypatch, tmp_path) -> None:
    runtime_dir = tmp_path / webview_runtime.RUNTIME_DIR_NAME
    runtime_dir.mkdir()
    (runtime_dir / webview_runtime.RUNTIME_EXE_NAME).write_bytes(b"test")
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    permission_paths = []

    monkeypatch.delenv("FILEBACKUP_WEBVIEW2_RUNTIME", raising=False)
    monkeypatch.setattr(webview_runtime, "application_root", lambda: tmp_path)
    monkeypatch.setattr(
        webview_runtime,
        "prepare_windows_10_appcontainer_permissions",
        lambda value: permission_paths.append(value),
    )

    selected = webview_runtime.configure_bundled_webview2_runtime()

    assert selected == runtime_dir.resolve()
    assert webview_runtime.webview.settings["WEBVIEW2_RUNTIME_PATH"] == str(
        runtime_dir.resolve()
    )
    assert permission_paths == [runtime_dir.resolve(), frontend_dir]


def test_gui_startup_error_is_written_to_local_log(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    try:
        raise RuntimeError("webview-startup-test")
    except RuntimeError as exc:
        log_path = report_gui_startup_error(exc)

    text = log_path.read_text(encoding="utf-8")
    assert "RuntimeError: webview-startup-test" in text
    assert "Traceback" in text


@pytest.mark.skipif(os.name != "nt", reason="Windows tray integration")
def test_server_webview_opens_real_index_file_without_query(
    monkeypatch, tmp_path
) -> None:
    index = tmp_path / "index.html"
    index.write_text("<html></html>", encoding="utf-8")
    captured: dict[str, object] = {}
    tray_state = {"started": False, "stopped": False}
    fake_window = SimpleNamespace()
    fake_api = SimpleNamespace(_window=None)

    class FakeTray:
        def __init__(self, window, **kwargs) -> None:
            assert window is fake_window

        def start(self) -> None:
            tray_state["started"] = True

        def stop(self) -> None:
            tray_state["stopped"] = True

    monkeypatch.setattr(webview_manager, "frontend_index", lambda: index)
    monkeypatch.setattr(
        webview_manager, "ServerDesktopApi", lambda config_path: fake_api
    )
    monkeypatch.setattr(
        webview_manager, "configure_bundled_webview2_runtime", lambda: None
    )
    monkeypatch.setattr(
        webview_manager.webview,
        "create_window",
        lambda title, url, **kwargs: (
            captured.update({"title": title, "url": url, **kwargs}) or fake_window
        ),
    )
    monkeypatch.setattr(webview_manager, "DesktopTrayController", FakeTray)
    monkeypatch.setattr(webview_manager.webview, "start", lambda **kwargs: None)

    webview_manager.run_server_webview(tmp_path / "server.json")

    assert captured["url"] == index.as_uri()
    assert "?" not in str(captured["url"])
    assert "%3F" not in str(captured["url"])
    assert fake_api._window is fake_window
    assert tray_state == {"started": True, "stopped": True}


@pytest.mark.skipif(os.name != "nt", reason="Windows Explorer integration")
def test_server_storage_paths_open_in_explorer_and_reject_other_paths(
    monkeypatch, tmp_path
) -> None:
    config = runtime.ServerRuntimeConfig(
        server_id="server-test",
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path / "data",
        admin_token="admin-token",
        client_tokens="client:token",
    )
    config_path = tmp_path / "server.json"
    runtime.save_runtime_config(config_path, config)
    api = webview_manager.ServerDesktopApi(config_path)
    opened: list[str] = []
    monkeypatch.setattr(webview_manager.os, "startfile", opened.append)

    target = config.data_dir / "storage"
    result = api.open_path(str(target))

    assert result == {"opened": True, "path": str(target.resolve())}
    assert target.is_dir()
    assert opened == [str(target.resolve())]
    with pytest.raises(ValueError, match="只能打开"):
        api.open_path(str(tmp_path / "not-managed"))


def test_server_settings_are_saved_and_running_server_is_restarted(
    monkeypatch, tmp_path
) -> None:
    previous = runtime.ServerRuntimeConfig(
        server_id="server-old",
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path / "old-data",
        admin_token="admin-token",
        client_tokens="",
    )
    config_path = tmp_path / "server.json"
    runtime.save_runtime_config(config_path, previous)
    api = webview_manager.ServerDesktopApi(config_path)
    stopped: list[runtime.ServerRuntimeConfig] = []

    monkeypatch.setattr(
        webview_manager,
        "read_health",
        lambda config, **kwargs: {"server_id": "server-old"},
    )
    monkeypatch.setattr(
        webview_manager,
        "stop_server_process",
        lambda config: stopped.append(config) or True,
    )
    monkeypatch.setattr(api, "start_server", lambda: {"status": "STARTING"})

    new_data_dir = tmp_path / "new-data"
    result = api.save_server_settings("server-new", str(new_data_dir))
    saved = runtime.load_runtime_config(config_path)

    assert stopped == [previous]
    assert saved.server_id == "server-new"
    assert saved.data_dir == new_data_dir.resolve()
    assert saved.admin_token == previous.admin_token
    assert new_data_dir.is_dir()
    assert result["status"] == "RESTARTING"
    assert result["server_id_changed"] is True
    assert result["data_dir_changed"] is True
    assert result["restarted"] is True


def test_server_settings_reject_unsafe_values(tmp_path) -> None:
    config = runtime.ServerRuntimeConfig(
        server_id="server-test",
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path / "data",
        admin_token="admin-token",
        client_tokens="",
    )
    config_path = tmp_path / "server.json"
    runtime.save_runtime_config(config_path, config)
    api = webview_manager.ServerDesktopApi(config_path)

    with pytest.raises(ValueError, match="Server ID"):
        api.save_server_settings("server id", str(tmp_path / "new-data"))
    with pytest.raises(ValueError, match="绝对路径"):
        api.save_server_settings("server-new", "relative-data")
    with pytest.raises(ValueError, match="磁盘根目录"):
        api.save_server_settings("server-new", str(Path(tmp_path.anchor)))


def test_server_data_directory_uses_native_folder_picker(tmp_path) -> None:
    config = runtime.ServerRuntimeConfig(
        server_id="server-test",
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path / "data",
        admin_token="admin-token",
        client_tokens="",
    )
    config_path = tmp_path / "server.json"
    runtime.save_runtime_config(config_path, config)
    api = webview_manager.ServerDesktopApi(config_path)
    captured: dict[str, object] = {}

    class FakeWindow:
        def create_file_dialog(self, dialog_type, **kwargs):
            captured.update({"dialog_type": dialog_type, **kwargs})
            return (tmp_path / "selected",)

    api._window = FakeWindow()
    result = api.choose_server_data_dir(str(tmp_path))

    assert result == [str(tmp_path / "selected")]
    assert captured["dialog_type"] == webview_manager.webview.FileDialog.FOLDER
    assert captured["directory"] == str(tmp_path)


def test_server_start_rejects_port_owned_by_different_server(
    monkeypatch, tmp_path
) -> None:
    config = runtime.ServerRuntimeConfig(
        server_id="server-test",
        host="127.0.0.1",
        port=8000,
        data_dir=tmp_path / "data",
        admin_token="admin-token",
        client_tokens="",
    )
    config_path = tmp_path / "server.json"
    runtime.save_runtime_config(config_path, config)
    api = webview_manager.ServerDesktopApi(config_path)
    monkeypatch.setattr(
        webview_manager,
        "read_health",
        lambda config, **kwargs: {"server_id": "another-server"},
    )

    with pytest.raises(RuntimeError, match="端口 8000"):
        api.start_server()
