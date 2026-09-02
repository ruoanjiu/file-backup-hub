from __future__ import annotations

from client.app.macos_menu_bar import MacOSClientMenuBar


def test_client_window_close_hides_without_quitting() -> None:
    class Window:
        hidden = False

        def hide(self) -> None:
            self.hidden = True

    window = Window()
    menu_bar = MacOSClientMenuBar(window, object())
    menu_bar.hide_window = window.hide  # type: ignore[method-assign]

    assert menu_bar.handle_window_closing() is False
    assert window.hidden is True

    menu_bar._quitting = True
    assert menu_bar.handle_window_closing() is True
