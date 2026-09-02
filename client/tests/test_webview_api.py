from __future__ import annotations

import json
from datetime import datetime, timezone

from client.app import webview_runtime
from client.app import webview_app
from client.app.transfer import SendTransferResult
from client.app.webview_app import ClientDesktopApi, to_bridge_data


def test_native_window_handle_is_not_a_public_bridge_attribute(tmp_path) -> None:
    api = ClientDesktopApi(tmp_path / "config.yaml")

    assert not hasattr(api, "window")
    assert api._window is None


def test_client_selects_bundled_webview2_runtime(monkeypatch, tmp_path) -> None:
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


def test_bridge_data_recursively_converts_paths_and_tuples(tmp_path) -> None:
    result = to_bridge_data(
        {
            "path": tmp_path / "bundle.tar.gz",
            "items": (tmp_path / "a", {"nested": tmp_path / "b"}),
            "created_at": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        }
    )

    assert result == {
        "path": str(tmp_path / "bundle.tar.gz"),
        "items": [str(tmp_path / "a"), {"nested": str(tmp_path / "b")}],
        "created_at": "2026-09-01T12:00:00+00:00",
    }
    json.dumps(result)


def test_send_result_with_windows_path_is_json_serializable(
    monkeypatch, tmp_path
) -> None:
    api = ClientDesktopApi(tmp_path / "config.yaml")
    monkeypatch.setattr(api, "_config", lambda: object())
    monkeypatch.setattr(
        webview_app,
        "send_transfer",
        lambda *args, **kwargs: SendTransferResult(
            transfer_id="transfer-test",
            server_id="server-a",
            status="AVAILABLE",
            file_count=1,
            total_size=12,
            workdir=tmp_path / "transfer-workdir",
        ),
    )

    result = api.send([str(tmp_path / "file.txt")], "receiver-pc", "server-a")

    assert result["workdir"] == str(tmp_path / "transfer-workdir")
    json.dumps(result)


def test_client_agent_bridge_delegates_to_embedded_runtime(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    api = ClientDesktopApi(config_path)
    calls = []
    monkeypatch.setattr(
        webview_app,
        "agent_status",
        lambda path: calls.append(("status", path)) or {"status": "RUNNING", "running": True},
    )
    monkeypatch.setattr(
        webview_app,
        "start_agent_process",
        lambda path: calls.append(("start", path)) or {"status": "STARTING", "running": True},
    )
    monkeypatch.setattr(
        webview_app,
        "stop_agent_process",
        lambda path: calls.append(("stop", path)) or {"status": "STOPPED", "running": False},
    )
    monkeypatch.setattr(
        webview_app,
        "restart_agent_process",
        lambda path: calls.append(("restart", path)) or {"status": "STARTING", "running": True},
    )

    assert api.get_agent_status()["status"] == "RUNNING"
    assert api.start_agent()["status"] == "STARTING"
    assert api.stop_agent()["status"] == "STOPPED"
    assert api.restart_agent()["status"] == "STARTING"
    assert calls == [
        ("status", config_path),
        ("start", config_path),
        ("stop", config_path),
        ("restart", config_path),
    ]
