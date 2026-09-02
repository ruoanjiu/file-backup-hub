from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Callable

from server.app.runtime import ServerRuntimeConfig, read_health


def server_is_running(config: ServerRuntimeConfig) -> bool:
    health = read_health(config, timeout=0.5)
    return bool(health and health.get("server_id") == config.server_id)


class MacOSServerMenuBar:
    """Native macOS menu bar controller for the Server Manager window."""

    def __init__(self, window: Any, api: Any) -> None:
        self.window = window
        self.api = api
        self._quitting = False
        self._stop_refresh = threading.Event()
        self._status_item: Any = None
        self._status_menu_item: Any = None
        self._start_menu_item: Any = None
        self._stop_menu_item: Any = None
        self._delegate: Any = None

    def install(self) -> None:
        """Schedule menu creation after pywebview has started Cocoa's event loop."""
        from PyObjCTools import AppHelper

        self.window.events.shown.wait(10)
        AppHelper.callAfter(self._install_on_main_thread)

    def handle_window_closing(self) -> bool:
        if self._quitting:
            self._stop_refresh.set()
            return True
        self.hide_window()
        return False

    def _install_on_main_thread(self) -> None:
        import AppKit

        controller = self

        class MenuDelegate(AppKit.NSObject):
            def openWindow_(self, sender: Any) -> None:
                controller.show_window()

            def startServer_(self, sender: Any) -> None:
                controller._run_action(controller.api.start_server)

            def stopServer_(self, sender: Any) -> None:
                controller._run_action(controller.api.stop_server)

            def quitManager_(self, sender: Any) -> None:
                controller.quit_manager()

        self._delegate = MenuDelegate.alloc().init()
        self._status_item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        button = self._status_item.button()
        image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(self._menu_icon_path()))
        if image is not None:
            image.setSize_(AppKit.NSMakeSize(18, 18))
            button.setImage_(image)
        button.setToolTip_("File Backup Server：正在检查")

        menu = AppKit.NSMenu.alloc().init()
        self._status_menu_item = self._disabled_item(AppKit, "Server 状态：正在检查…")
        menu.addItem_(self._status_menu_item)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        open_item = self._action_item(AppKit, "打开管理界面", "openWindow:")
        menu.addItem_(open_item)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        self._start_menu_item = self._action_item(AppKit, "启动 Server", "startServer:")
        self._stop_menu_item = self._action_item(AppKit, "停止 Server", "stopServer:")
        menu.addItem_(self._start_menu_item)
        menu.addItem_(self._stop_menu_item)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        quit_item = self._action_item(
            AppKit,
            "退出菜单栏（Server 继续运行）",
            "quitManager:",
        )
        menu.addItem_(quit_item)
        self._status_item.setMenu_(menu)

        self._refresh_status()
        threading.Thread(
            target=self._refresh_loop,
            name="server-menu-status",
            daemon=True,
        ).start()
        self._ensure_server_running()

    def _action_item(self, appkit: Any, title: str, selector: str) -> Any:
        item = appkit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title,
            selector,
            "",
        )
        item.setTarget_(self._delegate)
        return item

    @staticmethod
    def _disabled_item(appkit: Any, title: str) -> Any:
        item = appkit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title,
            None,
            "",
        )
        item.setEnabled_(False)
        return item

    def _menu_icon_path(self) -> Path:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        return base / "frontend" / "dist" / "app-icon.png"

    def _ensure_server_running(self) -> None:
        if not server_is_running(self.api.config):
            self._run_action(self.api.start_server)

    def _run_action(self, action: Callable[[], Any]) -> None:
        def run() -> None:
            try:
                action()
            finally:
                self._refresh_status()

        threading.Thread(target=run, name="server-menu-action", daemon=True).start()

    def _refresh_loop(self) -> None:
        while not self._stop_refresh.wait(3):
            self._refresh_status()

    def _refresh_status(self) -> None:
        running = server_is_running(self.api.config)

        def update() -> None:
            if self._status_menu_item is None or self._status_item is None:
                return
            status = "运行中" if running else "已停止"
            self._status_menu_item.setTitle_(f"Server 状态：{status}")
            self._start_menu_item.setEnabled_(not running)
            self._stop_menu_item.setEnabled_(running)
            button = self._status_item.button()
            button.setTitle_("" if running else " !")
            button.setToolTip_(f"File Backup Server：{status}")

        from PyObjCTools import AppHelper

        AppHelper.callAfter(update)

    def show_window(self) -> None:
        import AppKit
        from PyObjCTools import AppHelper

        def activate() -> None:
            AppKit.NSApplication.sharedApplication().setActivationPolicy_(
                AppKit.NSApplicationActivationPolicyRegular
            )
            self.window.show()

        AppHelper.callAfter(activate)

    def hide_window(self) -> None:
        import AppKit
        from PyObjCTools import AppHelper

        def hide() -> None:
            self.window.hide()
            AppKit.NSApplication.sharedApplication().setActivationPolicy_(
                AppKit.NSApplicationActivationPolicyAccessory
            )

        AppHelper.callAfter(hide)

    def quit_manager(self) -> None:
        import AppKit

        self._quitting = True
        self._stop_refresh.set()
        if self._status_item is not None:
            AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
            self._status_item = None
        self.window.destroy()
