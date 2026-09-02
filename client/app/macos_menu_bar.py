from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


class MacOSClientMenuBar:
    """Native macOS menu bar controller for the Client window."""

    def __init__(self, window: Any, api: Any) -> None:
        self.window = window
        self.api = api
        self._quitting = False
        self._status_item: Any = None
        self._delegate: Any = None

    def install(self) -> None:
        from PyObjCTools import AppHelper

        self.window.events.shown.wait(10)
        AppHelper.callAfter(self._install_on_main_thread)

    def handle_window_closing(self) -> bool:
        if self._quitting:
            return True
        self.hide_window()
        return False

    def _install_on_main_thread(self) -> None:
        import AppKit

        controller = self

        class MenuDelegate(AppKit.NSObject):
            def openWindow_(self, sender: Any) -> None:
                controller.show_window()

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
        button.setToolTip_("File Backup Client：运行中")

        menu = AppKit.NSMenu.alloc().init()
        status_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Client 管理器：运行中",
            None,
            "",
        )
        status_item.setEnabled_(False)
        menu.addItem_(status_item)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        menu.addItem_(self._action_item(AppKit, "打开 Client 界面", "openWindow:"))
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        menu.addItem_(
            self._action_item(
                AppKit,
                "退出 Client 管理器（独立 Agent 不受影响）",
                "quitManager:",
            )
        )
        self._status_item.setMenu_(menu)

    def _action_item(self, appkit: Any, title: str, selector: str) -> Any:
        item = appkit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title,
            selector,
            "",
        )
        item.setTarget_(self._delegate)
        return item

    def _menu_icon_path(self) -> Path:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        return base / "frontend" / "dist" / "app-icon.png"

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
        if self._status_item is not None:
            AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
            self._status_item = None
        self.window.destroy()
