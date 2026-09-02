from __future__ import annotations

import argparse
import multiprocessing
from pathlib import Path

from client.app.agent import run_agent
from client.app.config import default_config_path
from client.app.webview_app import run_client_webview


def main() -> None:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", action="store_true")
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args()
    if args.agent:
        run_agent(args.config)
        return
    run_client_webview(args.config)

if __name__ == "__main__":
    main()
