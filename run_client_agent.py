from __future__ import annotations

import argparse
from pathlib import Path

from client.app.agent import run_agent
from client.app.config import default_config_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args()
    run_agent(args.config)


if __name__ == "__main__":
    main()
