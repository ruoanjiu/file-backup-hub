from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ClientSection:
    machine_id: str
    timezone: str
    data_dir: Path
    temp_dir: Path


@dataclass(frozen=True)
class ServerSection:
    base_url: str
    token: str
    timeout_seconds: float = 60
    verify_tls: bool = True


@dataclass(frozen=True)
class BackupSection:
    schedule_enabled: bool = False
    schedule_time: str = "04:00"
    archive_format: str = "tar.gz"
    compression: bool = True
    retry_count: int = 3
    retry_interval_seconds: int = 5
    copy_stability_check: bool = True
    stability_check_interval_seconds: float = 1
    retention_hint_days: int = 90


@dataclass(frozen=True)
class RestoreSection:
    create_rollback_snapshot: bool = True
    allowed_roots: list[Path] = field(default_factory=list)
    rollback_dir: Path = Path("rollback")
    require_same_machine_id: bool = True


@dataclass(frozen=True)
class ProcessCheckConfig:
    enabled: bool = False
    process_name: str | None = None
    cmdline_keyword: str | None = None


@dataclass(frozen=True)
class StrategyRootConfig:
    path: Path
    recursive: bool = True
    include: list[str] = field(default_factory=lambda: ["*"])
    exclude: list[str] = field(default_factory=list)
    source_type: str = "auto"


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    enabled: bool
    roots: list[StrategyRootConfig]
    schedule_enabled: bool = False
    schedule_time: str = "04:00"
    process_check: ProcessCheckConfig = field(default_factory=ProcessCheckConfig)


@dataclass(frozen=True)
class AppConfig:
    client: ClientSection
    server: ServerSection
    backup: BackupSection
    restore: RestoreSection
    strategies: list[StrategyConfig]

    def enabled_strategies(self) -> list[StrategyConfig]:
        return [strategy for strategy in self.strategies if strategy.enabled]

    def scheduled_strategies(self) -> list[StrategyConfig]:
        return [
            strategy
            for strategy in self.strategies
            if strategy.enabled and strategy.schedule_enabled
        ]

    def get_strategy(self, name: str) -> StrategyConfig:
        for strategy in self.strategies:
            if strategy.name == name:
                return strategy
        raise ValueError(f"Strategy not found: {name}")


def default_config_path() -> Path:
    if os.name == "nt":
        program_data = Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
        return program_data / "TradingBackupClient" / "config.yaml"
    return Path("/etc/trading-backup-client/config.yaml")


def _as_mapping(data: Any, name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be a mapping")
    return data


def _as_list(data: Any, name: str) -> list[Any]:
    if not isinstance(data, list):
        raise ValueError(f"{name} must be a list")
    return data


def _path(value: Any, name: str, *, require_absolute: bool) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string path")
    path = Path(value).expanduser()
    if require_absolute and not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path: {value}")
    return path


def _string_list(value: Any, name: str, default: list[str] | None = None) -> list[str]:
    if value is None:
        return list(default or [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be a list of strings")
    return value


def _load_process_check(raw: dict[str, Any]) -> ProcessCheckConfig:
    process_raw = raw.get("process_check") or {}
    process = _as_mapping(process_raw, "process_check")
    return ProcessCheckConfig(
        enabled=bool(process.get("enabled", False)),
        process_name=process.get("process_name"),
        cmdline_keyword=process.get("cmdline_keyword"),
    )


def _load_strategy_root(raw: Any, index: int) -> StrategyRootConfig:
    root = _as_mapping(raw, f"strategies[].roots[{index}]")
    include = _string_list(root.get("include"), f"strategies[].roots[{index}].include", ["*"])
    exclude = _string_list(root.get("exclude"), f"strategies[].roots[{index}].exclude", [])
    return StrategyRootConfig(
        path=_path(root.get("path"), f"strategies[].roots[{index}].path", require_absolute=True),
        recursive=bool(root.get("recursive", True)),
        include=include or ["*"],
        exclude=exclude,
        source_type=str(root.get("source_type", "auto")),
    )


def _load_strategy(raw: Any, index: int) -> StrategyConfig:
    strategy = _as_mapping(raw, f"strategies[{index}]")
    name = strategy.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"strategies[{index}].name must be a non-empty string")
    roots = [
        _load_strategy_root(root_raw, root_index)
        for root_index, root_raw in enumerate(_as_list(strategy.get("roots"), "roots"))
    ]
    if not roots:
        raise ValueError(f"strategy {name} must define at least one root")
    return StrategyConfig(
        name=name,
        enabled=bool(strategy.get("enabled", True)),
        roots=roots,
        schedule_enabled=bool(strategy.get("schedule_enabled", False)),
        schedule_time=str(strategy.get("schedule_time", "04:00")),
        process_check=_load_process_check(strategy),
    )


def load_config(path: Path) -> AppConfig:
    with path.open("r", encoding="utf-8") as file_obj:
        data = yaml.safe_load(file_obj) or {}

    root = _as_mapping(data, "config")
    client_raw = _as_mapping(root.get("client"), "client")
    server_raw = _as_mapping(root.get("server"), "server")
    backup_raw = _as_mapping(root.get("backup") or {}, "backup")
    restore_raw = _as_mapping(root.get("restore") or {}, "restore")

    data_dir = _path(client_raw.get("data_dir"), "client.data_dir", require_absolute=True)
    temp_dir_raw = client_raw.get("temp_dir")
    temp_dir = (
        _path(temp_dir_raw, "client.temp_dir", require_absolute=True)
        if temp_dir_raw
        else data_dir / "tmp"
    )

    machine_id = client_raw.get("machine_id")
    if not isinstance(machine_id, str) or not machine_id.strip():
        raise ValueError("client.machine_id must be a non-empty string")

    base_url = server_raw.get("base_url")
    token = server_raw.get("token")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("server.base_url must be a non-empty string")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("server.token must be a non-empty string")

    rollback_dir_raw = restore_raw.get("rollback_dir")
    restore = RestoreSection(
        create_rollback_snapshot=bool(restore_raw.get("create_rollback_snapshot", True)),
        allowed_roots=[
            _path(item, "restore.allowed_roots[]", require_absolute=True)
            for item in _string_list(restore_raw.get("allowed_roots"), "restore.allowed_roots", [])
        ],
        rollback_dir=(
            _path(rollback_dir_raw, "restore.rollback_dir", require_absolute=True)
            if rollback_dir_raw
            else data_dir / "rollback"
        ),
        require_same_machine_id=bool(restore_raw.get("require_same_machine_id", True)),
    )

    return AppConfig(
        client=ClientSection(
            machine_id=machine_id,
            timezone=str(client_raw.get("timezone", "Asia/Shanghai")),
            data_dir=data_dir,
            temp_dir=temp_dir,
        ),
        server=ServerSection(
            base_url=base_url.rstrip("/"),
            token=token,
            timeout_seconds=float(server_raw.get("timeout_seconds", 60)),
            verify_tls=bool(server_raw.get("verify_tls", True)),
        ),
        backup=BackupSection(
            schedule_enabled=bool(backup_raw.get("schedule_enabled", False)),
            schedule_time=str(backup_raw.get("schedule_time", "04:00")),
            archive_format=str(backup_raw.get("archive_format", "tar.gz")),
            compression=bool(backup_raw.get("compression", True)),
            retry_count=int(backup_raw.get("retry_count", 3)),
            retry_interval_seconds=int(backup_raw.get("retry_interval_seconds", 5)),
            copy_stability_check=bool(backup_raw.get("copy_stability_check", True)),
            stability_check_interval_seconds=float(
                backup_raw.get("stability_check_interval_seconds", 1)
            ),
            retention_hint_days=int(backup_raw.get("retention_hint_days", 90)),
        ),
        restore=restore,
        strategies=[
            _load_strategy(strategy_raw, index)
            for index, strategy_raw in enumerate(_as_list(root.get("strategies"), "strategies"))
        ],
    )
