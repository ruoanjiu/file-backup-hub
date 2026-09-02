from __future__ import annotations

import csv
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from client.app.config import AppConfig, load_config


AGENT_PID_FILE_NAME = "client-agent.pid"
AGENT_STARTING_PID_FILE_NAME = "client-agent-starting.pid"
LEGACY_AGENT_EXE_NAME = "FileBackupClientAgent.exe"


def _pid_paths(config: AppConfig) -> tuple[Path, Path]:
    return (
        config.client.data_dir / AGENT_PID_FILE_NAME,
        config.client.data_dir / AGENT_STARTING_PID_FILE_NAME,
    )


def _read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return None
    return value if value > 0 else None


def _process_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _remove_stale_pid(path: Path) -> None:
    pid = _read_pid(path)
    if pid is None or not _process_running(pid):
        path.unlink(missing_ok=True)


def _legacy_agent_pids() -> list[int]:
    if os.name != "nt":
        return []
    completed = subprocess.run(
        [
            "tasklist.exe",
            "/FI",
            f"IMAGENAME eq {LEGACY_AGENT_EXE_NAME}",
            "/FO",
            "CSV",
            "/NH",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    pids: list[int] = []
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) < 2 or row[0].lower() != LEGACY_AGENT_EXE_NAME.lower():
            continue
        try:
            pids.append(int(row[1]))
        except ValueError:
            continue
    return pids


def agent_status(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        return {
            "status": "UNCONFIGURED",
            "running": False,
            "mode": "embedded",
            "scheduled_tasks": 0,
        }
    try:
        config = load_config(config_path)
    except Exception as exc:
        return {
            "status": "CONFIG_ERROR",
            "running": False,
            "mode": "embedded",
            "scheduled_tasks": 0,
            "error": str(exc),
        }
    pid_path, starting_path = _pid_paths(config)
    _remove_stale_pid(pid_path)
    _remove_stale_pid(starting_path)
    pid = _read_pid(pid_path)
    starting_pid = _read_pid(starting_path)
    legacy_pids = [pid for pid in _legacy_agent_pids() if _process_running(pid)]
    scheduled_tasks = len(config.scheduled_tasks())
    if _process_running(pid):
        return {
            "status": "RUNNING",
            "running": True,
            "mode": "embedded",
            "pid": pid,
            "scheduled_tasks": scheduled_tasks,
        }
    if _process_running(starting_pid):
        return {
            "status": "STARTING",
            "running": True,
            "mode": "embedded",
            "pid": starting_pid,
            "scheduled_tasks": scheduled_tasks,
        }
    if legacy_pids:
        return {
            "status": "RUNNING_LEGACY",
            "running": True,
            "mode": "legacy",
            "pid": legacy_pids[0],
            "scheduled_tasks": scheduled_tasks,
        }
    if not config.enabled_servers():
        return {
            "status": "NO_SERVER",
            "running": False,
            "mode": "embedded",
            "scheduled_tasks": scheduled_tasks,
        }
    return {
        "status": "STOPPED",
        "running": False,
        "mode": "embedded",
        "scheduled_tasks": scheduled_tasks,
    }


def _agent_command(config_path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--agent", "--config", str(config_path)]
    project_root = Path(__file__).resolve().parents[2]
    return [
        sys.executable,
        str(project_root / "run_client_gui.py"),
        "--agent",
        "--config",
        str(config_path),
    ]


def _terminate_process_tree(pid: int) -> bool:
    if not _process_running(pid):
        return True
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0 or not _process_running(pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    return True


def stop_agent_process(config_path: Path, *, timeout: float = 8.0) -> dict[str, Any]:
    if not config_path.is_file():
        return agent_status(config_path)
    config = load_config(config_path)
    pid_path, starting_path = _pid_paths(config)
    pids = [
        _read_pid(starting_path),
        _read_pid(pid_path),
        *_legacy_agent_pids(),
    ]
    failed: list[int] = []
    for pid in dict.fromkeys(value for value in pids if value):
        if not _terminate_process_tree(pid):
            failed.append(pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_process_running(pid) for pid in pids if pid):
            break
        time.sleep(0.1)
    _remove_stale_pid(pid_path)
    _remove_stale_pid(starting_path)
    result = agent_status(config_path)
    if failed or result.get("running"):
        return {**result, "status": "STOP_FAILED", "failed_pids": failed}
    return {**result, "status": "STOPPED", "running": False}


def start_agent_process(config_path: Path) -> dict[str, Any]:
    status = agent_status(config_path)
    if status["status"] in {"RUNNING", "STARTING"}:
        return status
    if status["status"] in {"UNCONFIGURED", "CONFIG_ERROR", "NO_SERVER"}:
        return status
    if status["status"] == "RUNNING_LEGACY":
        stopped = stop_agent_process(config_path)
        if stopped.get("running"):
            return stopped

    config = load_config(config_path)
    _, starting_path = _pid_paths(config)
    starting_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir = config.client.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = (log_dir / "client-agent-launch.log").open(
        "a",
        encoding="utf-8",
    )
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "cwd": str(Path(sys.executable).resolve().parent),
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(_agent_command(config_path), **kwargs)
        temp_path = starting_path.with_suffix(starting_path.suffix + ".tmp")
        temp_path.write_text(f"{process.pid}\n", encoding="utf-8")
        temp_path.replace(starting_path)
    finally:
        log_file.close()
    return {
        "status": "STARTING",
        "running": True,
        "mode": "embedded",
        "pid": process.pid,
        "scheduled_tasks": len(config.scheduled_tasks()),
    }


def restart_agent_process(config_path: Path) -> dict[str, Any]:
    stopped = stop_agent_process(config_path)
    if stopped.get("running"):
        return stopped
    return start_agent_process(config_path)


def claim_agent_instance(config: AppConfig) -> bool:
    pid_path, starting_path = _pid_paths(config)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            descriptor = os.open(
                pid_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            existing_pid = _read_pid(pid_path)
            if _process_running(existing_pid):
                return existing_pid == os.getpid()
            pid_path.unlink(missing_ok=True)
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as file_obj:
            file_obj.write(f"{os.getpid()}\n")
        starting_path.unlink(missing_ok=True)
        return True
    return False


def release_agent_instance(config: AppConfig) -> None:
    pid_path, starting_path = _pid_paths(config)
    if _read_pid(pid_path) == os.getpid():
        pid_path.unlink(missing_ok=True)
    _remove_stale_pid(starting_path)
