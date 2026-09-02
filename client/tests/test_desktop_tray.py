from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from desktop_tray import DesktopTrayController


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWindow:
    def __init__(self) -> None:
        self.events = SimpleNamespace(closing=FakeEvent(), closed=FakeEvent())
        self.shown = 0
        self.hidden = 0
        self.destroyed = 0

    def show(self) -> None:
        self.shown += 1

    def hide(self) -> None:
        self.hidden += 1

    def destroy(self) -> None:
        self.destroyed += 1


class FakeMenu:
    SEPARATOR = object()

    def __init__(self, *items) -> None:
        self.items = items


class FakeMenuItem:
    def __init__(self, text, action, **kwargs) -> None:
        self.text = text
        self.action = action
        self.kwargs = kwargs


class FakeIcon:
    def __init__(self, name, image, title, menu) -> None:
        self.name = name
        self.image = image
        self.title = title
        self.menu = menu
        self.ran = threading.Event()
        self.stopped = False
        self.notifications = []

    def run(self) -> None:
        self.ran.set()

    def stop(self) -> None:
        self.stopped = True

    def notify(self, message, title) -> None:
        self.notifications.append((message, title))


def test_tray_hides_on_window_close_and_only_exits_from_menu(tmp_path: Path) -> None:
    window = FakeWindow()
    exit_calls = []
    module = SimpleNamespace(Menu=FakeMenu, MenuItem=FakeMenuItem, Icon=FakeIcon)
    controller = DesktopTrayController(
        window,
        name="FileBackupTest",
        title="File Backup Test 正在运行",
        icon_path=tmp_path / "missing.png",
        on_exit=lambda: exit_calls.append("agent-stopped"),
        tray_module=module,
    )

    controller.start()
    icon = controller._icon
    assert icon is not None
    assert icon.ran.wait(1)
    assert len(window.events.closing.handlers) == 1
    assert len(window.events.closed.handlers) == 1

    assert window.events.closing.handlers[0]() is False
    assert window.hidden == 1
    assert len(icon.notifications) == 1
    controller._show()
    assert window.shown == 1

    controller._exit()
    assert icon.stopped is True
    assert window.destroyed == 1
    assert exit_calls == ["agent-stopped"]
    assert controller._on_closing() is True
