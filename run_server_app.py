from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from server.app.main import create_app
from server.app.runtime import (
    build_settings,
    default_server_config_path,
    load_runtime_config,
)


def run_headless(config_path: Path) -> None:
    config = load_runtime_config(config_path)
    config.data_dir.mkdir(parents=True, exist_ok=True)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--config", type=Path, default=default_server_config_path())
    args = parser.parse_args()
    if args.headless:
        run_headless(args.config)
    else:
        from server.app.webview_manager import run_server_webview

        run_server_webview(args.config)


if __name__ == "__main__":
    main()
