from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import webview


RUNTIME_DIR_NAME = "webview2-runtime"
RUNTIME_EXE_NAME = "msedgewebview2.exe"


def application_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))


def bundled_webview2_runtime_path() -> Path | None:
    configured = os.getenv("FILEBACKUP_WEBVIEW2_RUNTIME", "").strip()
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(application_root() / RUNTIME_DIR_NAME)
    for candidate in candidates:
        if (candidate / RUNTIME_EXE_NAME).is_file():
            return candidate.resolve()
    return None


def _is_windows_10() -> bool:
    return os.name == "nt" and sys.getwindowsversion().build < 22000


def prepare_windows_10_appcontainer_permissions(runtime_path: Path) -> None:
    if not _is_windows_10():
        return
    for grant in ("*S-1-15-2-2:(OI)(CI)(RX)", "*S-1-15-2-1:(OI)(CI)(RX)"):
        completed = subprocess.run(
            ["icacls", str(runtime_path), "/grant", grant, "/T", "/C", "/Q"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                "无法为内置WebView2配置Windows 10 AppContainer读取权限："
                + (message or str(completed.returncode))
            )


def configure_bundled_webview2_runtime() -> Path | None:
    runtime_path = bundled_webview2_runtime_path()
    if runtime_path is None:
        return None
    prepare_windows_10_appcontainer_permissions(runtime_path)
    frontend_path = application_root() / "frontend"
    if frontend_path.is_dir():
        prepare_windows_10_appcontainer_permissions(frontend_path)
    webview.settings["WEBVIEW2_RUNTIME_PATH"] = str(runtime_path)
    return runtime_path
