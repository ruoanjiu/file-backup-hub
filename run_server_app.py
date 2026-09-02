from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import TextIO

import uvicorn

from server.app.main import create_app
from server.app.runtime import (
    build_settings,
    default_server_config_path,
    load_runtime_config,
)


def ensure_headless_stdio(data_dir: Path) -> TextIO | None:
    if sys.stdout is not None and sys.stderr is not None:
        return None
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_handle = (log_dir / "server.log").open(
        "a", encoding="utf-8", buffering=1
    )
    if sys.stdout is None:
        sys.stdout = log_handle
    if sys.stderr is None:
        sys.stderr = log_handle
    return log_handle


def run_headless(config_path: Path) -> None:
    config = load_runtime_config(config_path)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    log_handle = ensure_headless_stdio(config.data_dir)
    config.pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    try:
        uvicorn.run(
            create_app(build_settings(config)),
            host=config.host,
            port=config.port,
            log_level="info",
        )
    finally:
        try:
            if config.pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                config.pid_file.unlink()
        except FileNotFoundError:
            pass
        if log_handle is not None:
            if sys.stdout is log_handle:
                sys.stdout = None
            if sys.stderr is log_handle:
                sys.stderr = None
            log_handle.close()


def report_gui_startup_error(exc: BaseException) -> Path:
    base = Path(os.getenv("LOCALAPPDATA", str(Path.home())))
    log_path = base / "FileBackupServer" / "logs" / "server-manager-startup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"[{timestamp}] {type(exc).__name__}: {exc}\n")
        log.write(traceback.format_exc())
        log.write("\n")
    if os.name == "nt" and getattr(sys, "frozen", False):
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            f"File Backup Server无法启动。\n\n{exc}\n\n日志：{log_path}",
            "File Backup Server",
            0x10,
        )
    return log_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--config", type=Path, default=default_server_config_path())
    args = parser.parse_args()
    if args.headless:
        run_headless(args.config)
    else:
        from server.app.webview_manager import run_server_webview

        try:
            run_server_webview(args.config)
        except Exception as exc:
            report_gui_startup_error(exc)
            raise


if __name__ == "__main__":
    main()
