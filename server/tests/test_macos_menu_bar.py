from __future__ import annotations

from pathlib import Path

from server.app.macos_menu_bar import MacOSServerMenuBar, server_is_running
from server.app.runtime import ServerRuntimeConfig


def test_server_is_running_matches_server_identity(monkeypatch) -> None:
    config = ServerRuntimeConfig(
        server_id="server-a",
        host="0.0.0.0",
        port=8000,
        data_dir=Path("/tmp/server-a"),
        admin_token="admin",
        client_tokens="client:token",
    )
    monkeypatch.setattr(
        "server.app.macos_menu_bar.read_health",
        lambda *_args, **_kwargs: {"server_id": "server-a"},
    )
    assert server_is_running(config) is True

    monkeypatch.setattr(
        "server.app.macos_menu_bar.read_health",
        lambda *_args, **_kwargs: {"server_id": "another-server"},
    )
    assert server_is_running(config) is False


def test_window_close_hides_manager_without_quitting() -> None:
    class Window:
        hidden = False

        def hide(self) -> None:
            self.hidden = True

    class Api:
        pass

    window = Window()
    menu_bar = MacOSServerMenuBar(window, Api())
    menu_bar.hide_window = window.hide  # type: ignore[method-assign]

    assert menu_bar.handle_window_closing() is False
    assert window.hidden is True

    menu_bar._quitting = True
    assert menu_bar.handle_window_closing() is True
