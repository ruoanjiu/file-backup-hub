from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from client.app import agent_runtime
from client.app.config import load_config


def write_config(path: Path, *, enabled: bool = True, scheduled: bool = True) -> Path:
    data_dir = path.parent / "client-data"
    payload = {
        "client": {
            "machine_id": "agent-test-pc",
            "display_name": "Agent Test",
            "timezone": "Asia/Shanghai",
            "data_dir": str(data_dir),
            "temp_dir": str(data_dir / "tmp"),
            "outbox_dir": str(data_dir / "outbox"),
        },
        "servers": [
            {
                "id": "server-a",
                "name": "Server A",
                "base_url": "https://server-a.example.test",
                "token": "dummy-token",
                "enabled": enabled,
            }
        ],
        "backup": {"required_copies": 1 if enabled else 0},
        "restore": {"allowed_roots": [], "rollback_dir": str(data_dir / "rollback")},
        "transfer": {
            "inbox_dir": str(data_dir / "inbox"),
            "temp_dir": str(data_dir / "transfer-tmp"),
        },
        "tasks": [
            {
                "name": "daily",
                "enabled": True,
                "schedule_enabled": scheduled,
                "schedule_time": "04:00",
                "roots": [
                    {
                        "path": str(path.parent / "source"),
                        "recursive": True,
                        "include": ["*"],
                        "exclude": [],
                    }
                ],
            }
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_start_agent_uses_same_frozen_executable_and_embedded_mode(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = write_config(tmp_path / "config.yaml")
    calls: list[tuple[list[str], dict]] = []

    class FakeProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(agent_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(agent_runtime.sys, "executable", str(tmp_path / "FileBackupClient.exe"))
    monkeypatch.setattr(agent_runtime, "_legacy_agent_pids", lambda: [])
    monkeypatch.setattr(agent_runtime, "_process_running", lambda pid: False)
    monkeypatch.setattr(agent_runtime.subprocess, "Popen", fake_popen)

    result = agent_runtime.start_agent_process(config_path)

    assert result == {
        "status": "STARTING",
        "running": True,
        "mode": "embedded",
        "pid": 4321,
        "scheduled_tasks": 1,
    }
    assert calls[0][0] == [
        str(tmp_path / "FileBackupClient.exe"),
        "--agent",
        "--config",
        str(config_path),
    ]
    config = load_config(config_path)
    _, starting_path = agent_runtime._pid_paths(config)
    assert starting_path.read_text(encoding="utf-8").strip() == "4321"


def test_agent_instance_lock_rejects_another_running_pid(monkeypatch, tmp_path: Path) -> None:
    config_path = write_config(tmp_path / "config.yaml")
    config = load_config(config_path)
    pid_path, _ = agent_runtime._pid_paths(config)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("9999\n", encoding="utf-8")
    monkeypatch.setattr(agent_runtime, "_process_running", lambda pid: pid == 9999)

    assert agent_runtime.claim_agent_instance(config) is False
    assert pid_path.read_text(encoding="utf-8").strip() == "9999"


def test_agent_status_reports_no_server_without_starting_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = write_config(tmp_path / "config.yaml", enabled=False)
    monkeypatch.setattr(agent_runtime, "_legacy_agent_pids", lambda: [])

    assert agent_runtime.agent_status(config_path) == {
        "status": "NO_SERVER",
        "running": False,
        "mode": "embedded",
        "scheduled_tasks": 1,
    }
    assert agent_runtime.start_agent_process(config_path)["status"] == "NO_SERVER"


def test_restart_stops_existing_agent_before_starting(monkeypatch, tmp_path: Path) -> None:
    config_path = write_config(tmp_path / "config.yaml")
    calls: list[str] = []
    monkeypatch.setattr(
        agent_runtime,
        "stop_agent_process",
        lambda path: calls.append(f"stop:{path}") or {"status": "STOPPED", "running": False},
    )
    monkeypatch.setattr(
        agent_runtime,
        "start_agent_process",
        lambda path: calls.append(f"start:{path}") or {"status": "STARTING", "running": True},
    )

    result = agent_runtime.restart_agent_process(config_path)

    assert result["status"] == "STARTING"
    assert calls == [f"stop:{config_path}", f"start:{config_path}"]
