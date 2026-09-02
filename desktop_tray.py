from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pystray
from PIL import Image, ImageDraw


class DesktopTrayController:
    """Keep a pywebview desktop app available from the Windows notification area."""

    def __init__(
        self,
        window: Any,
        *,
        name: str,
        title: str,
        icon_path: Path | None = None,
        on_exit: Callable[[], Any] | None = None,
        tray_module: Any = pystray,
    ) -> None:
        self._window = window
        self._name = name
        self._title = title
        self._icon_path = icon_path
        self._on_exit = on_exit
        self._tray_module = tray_module
        self._icon: Any | None = None
        self._thread: threading.Thread | None = None
        self._exiting = False
        self._hide_notice_sent = False

    def start(self) -> None:
        if self._icon is not None:
            return
        self._window.events.closing += self._on_closing
        self._window.events.closed += self._on_closed
        menu = self._tray_module.Menu(
            self._tray_module.MenuItem("打开窗口", self._show, default=True),
            self._tray_module.MenuItem("隐藏窗口", self._hide),
            self._tray_module.Menu.SEPARATOR,
            self._tray_module.MenuItem("退出程序", self._exit),
        )
        self._icon = self._tray_module.Icon(
            self._name,
            self._load_image(),
            self._title,
            menu,
        )
        self._thread = threading.Thread(
            target=self._icon.run,
            name=f"{self._name}-tray",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass

    def _load_image(self) -> Image.Image:
        if self._icon_path is not None and self._icon_path.is_file():
            with Image.open(self._icon_path) as source:
                return source.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
        image = Image.new("RGBA", (64, 64), (20, 110, 245, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((13, 14, 51, 49), radius=7, fill="white")
        draw.rectangle((20, 22, 44, 27), fill=(20, 110, 245, 255))
        draw.ellipse((42, 42, 57, 57), fill=(31, 199, 123, 255), outline="white", width=2)
        return image

    def _show(self, _icon: Any = None, _item: Any = None) -> None:
        if not self._exiting:
            self._window.show()

    def _hide(self, _icon: Any = None, _item: Any = None) -> None:
        if not self._exiting:
            self._window.hide()

    def _exit(self, _icon: Any = None, _item: Any = None) -> None:
        if self._exiting:
            return
        self._exiting = True
        self.stop()
        if self._on_exit is not None:
            try:
                self._on_exit()
            except Exception:
                pass
        self._window.destroy()

    def _on_closing(self) -> bool:
        if self._exiting:
            return True
        self._window.hide()
        if not self._hide_notice_sent and self._icon is not None:
            try:
                self._icon.notify(
                    "程序仍在后台运行。双击托盘图标可重新打开，右键可退出。",
                    self._title,
                )
            except Exception:
                pass
            self._hide_notice_sent = True
        return False

    def _on_closed(self) -> None:
        self.stop()
