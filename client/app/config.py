from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ClientSection:
    machine_id: str
    display_name: str
    timezone: str
    data_dir: Path
    temp_dir: Path
    outbox_dir: Path


@dataclass(frozen=True)
class ServerSection:
    base_url: str
    token: str
    timeout_seconds: float = 60
    verify_tls: bool = True
    id: str = "server-1"
    name: str = "Server 1"
    enabled: bool = True


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
    required_copies: int = 1
    keep_local_until_all_uploaded: bool = True


@dataclass(frozen=True)
class RestoreSection:
    create_rollback_snapshot: bool = True
    allowed_roots: list[Path] = field(default_factory=list)
    rollback_dir: Path = Path("rollback")
    require_same_machine_id: bool = True


@dataclass(frozen=True)
class TransferSection:
    inbox_dir: Path
    temp_dir: Path
    allowed_send_roots: list[Path] = field(default_factory=list)
    require_confirmation: bool = True
    overwrite_existing: bool = False


@dataclass(frozen=True)
class ProcessCheckConfig:
    enabled: bool = False
    process_name: str | None = None
    cmdline_keyword: str | None = None


@dataclass(frozen=True)
class TaskRootConfig:
    path: Path
    recursive: bool = True
    include: list[str] = field(default_factory=lambda: ["*"])
    exclude: list[str] = field(default_factory=list)
    source_type: str = "auto"


@dataclass(frozen=True)
class TaskConfig:
    name: str
    enabled: bool
    roots: list[TaskRootConfig]
    schedule_enabled: bool = False
    schedule_time: str = "04:00"
    process_check: ProcessCheckConfig = field(default_factory=ProcessCheckConfig)


@dataclass(frozen=True)
class AppConfig:
    client: ClientSection
    servers: list[ServerSection]
    backup: BackupSection
    restore: RestoreSection
    transfer: TransferSection
    tasks: list[TaskConfig]

    @property
    def server(self) -> ServerSection:
        """Backward-compatible access to the first enabled Server."""
        for server in self.servers:
            if server.enabled:
                return server
        raise ValueError("No enabled Server is configured")

    def enabled_servers(self) -> list[ServerSection]:
        return [server for server in self.servers if server.enabled]

    def get_server(self, server_id: str) -> ServerSection:
        for server in self.servers:
            if server.id == server_id and server.enabled:
                return server
        raise ValueError(f"Enabled Server not found: {server_id}")

    def enabled_tasks(self) -> list[TaskConfig]:
        return [task for task in self.tasks if task.enabled]

    def scheduled_tasks(self) -> list[TaskConfig]:
        return [
            task
            for task in self.tasks
            if task.enabled and task.schedule_enabled
        ]

    def get_task(self, name: str) -> TaskConfig:
        for task in self.tasks:
            if task.name == name:
                return task
        raise ValueError(f"Task not found: {name}")


def default_config_path() -> Path:
    if os.name == "nt":
        program_data = Path(os.getenv("PROGRAMDATA", "C:/ProgramData"))
        return program_data / "FileBackupClient" / "config.yaml"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "FileBackupClient" / "config.yaml"
    return Path("/etc/file-backup-client/config.yaml")


def default_client_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "FileBackupClient"
    if sys.platform == "darwin":
        return Path("/Users/Shared/FileBackupClient")
    return Path.home() / ".local" / "share" / "file-backup-client"


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


def _load_task_root(raw: Any, index: int) -> TaskRootConfig:
    root = _as_mapping(raw, f"tasks[].roots[{index}]")
    include = _string_list(root.get("include"), f"tasks[].roots[{index}].include", ["*"])
    exclude = _string_list(root.get("exclude"), f"tasks[].roots[{index}].exclude", [])
    return TaskRootConfig(
        path=_path(root.get("path"), f"tasks[].roots[{index}].path", require_absolute=True),
        recursive=bool(root.get("recursive", True)),
        include=include or ["*"],
        exclude=exclude,
        source_type=str(root.get("source_type", "auto")),
    )


def _load_task(raw: Any, index: int) -> TaskConfig:
    task = _as_mapping(raw, f"tasks[{index}]")
    name = task.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"tasks[{index}].name must be a non-empty string")
    roots = [
        _load_task_root(root_raw, root_index)
        for root_index, root_raw in enumerate(_as_list(task.get("roots"), "roots"))
    ]
    if not roots:
        raise ValueError(f"task {name} must define at least one root")
    return TaskConfig(
        name=name,
        enabled=bool(task.get("enabled", True)),
        roots=roots,
        schedule_enabled=bool(task.get("schedule_enabled", False)),
        schedule_time=str(task.get("schedule_time", "04:00")),
        process_check=_load_process_check(task),
    )


def _load_server(raw: Any, index: int) -> ServerSection:
    server = _as_mapping(raw, f"servers[{index}]")
    base_url = server.get("base_url")
    token = server.get("token") or ""
    enabled = bool(server.get("enabled", True))
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError(f"servers[{index}].base_url must be a non-empty string")
    if not isinstance(token, str) or (enabled and not token.strip()):
        raise ValueError(f"servers[{index}].token must be a non-empty string")
    server_id = server.get("id") or f"server-{index + 1}"
    if not isinstance(server_id, str) or not server_id.strip():
        raise ValueError(f"servers[{index}].id must be a non-empty string")
    return ServerSection(
        base_url=base_url.rstrip("/"),
        token=token.strip(),
        timeout_seconds=float(server.get("timeout_seconds", 60)),
        verify_tls=bool(server.get("verify_tls", True)),
        id=server_id.strip(),
        name=str(server.get("name") or server_id).strip(),
        enabled=enabled,
    )


def _assert_writable_paths_outside_sources(
    tasks: list[TaskConfig],
    writable_paths: list[Path],
) -> None:
    """The backup agent must never place its own writable data under a source root."""
    for task in tasks:
        for root in task.roots:
            source = root.path.expanduser().resolve(strict=False)
            for writable in writable_paths:
                target = writable.expanduser().resolve(strict=False)
                if target == source or target.is_relative_to(source):
                    raise ValueError(
                        f"Client writable path must be outside backup source {source}: {target}"
                    )


def load_config(path: Path) -> AppConfig:
    with path.open("r", encoding="utf-8") as file_obj:
        data = yaml.safe_load(file_obj) or {}

    root = _as_mapping(data, "config")
    client_raw = _as_mapping(root.get("client"), "client")
    backup_raw = _as_mapping(root.get("backup") or {}, "backup")
    restore_raw = _as_mapping(root.get("restore") or {}, "restore")
    transfer_raw = _as_mapping(root.get("transfer") or {}, "transfer")

    data_dir = _path(client_raw.get("data_dir"), "client.data_dir", require_absolute=True)
    temp_dir_raw = client_raw.get("temp_dir")
    temp_dir = (
        _path(temp_dir_raw, "client.temp_dir", require_absolute=True)
        if temp_dir_raw
        else data_dir / "tmp"
    )
    outbox_dir_raw = client_raw.get("outbox_dir")
    outbox_dir = (
        _path(outbox_dir_raw, "client.outbox_dir", require_absolute=True)
        if outbox_dir_raw
        else data_dir / "outbox"
    )

    machine_id = client_raw.get("machine_id")
    if not isinstance(machine_id, str) or not machine_id.strip():
        raise ValueError("client.machine_id must be a non-empty string")

    if root.get("servers") is not None:
        servers = [
            _load_server(server_raw, index)
            for index, server_raw in enumerate(_as_list(root.get("servers"), "servers"))
        ]
    else:
        # Keep old single-Server config files working during migration.
        servers = [_load_server(_as_mapping(root.get("server"), "server"), 0)]
    server_ids = [server.id for server in servers]
    if len(server_ids) != len(set(server_ids)):
        raise ValueError("Server IDs must be unique")

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

    inbox_dir_raw = transfer_raw.get("inbox_dir")
    transfer_temp_raw = transfer_raw.get("temp_dir")
    transfer = TransferSection(
        inbox_dir=(
            _path(inbox_dir_raw, "transfer.inbox_dir", require_absolute=True)
            if inbox_dir_raw
            else Path.home() / "Downloads" / "FileBackup Inbox"
        ),
        temp_dir=(
            _path(transfer_temp_raw, "transfer.temp_dir", require_absolute=True)
            if transfer_temp_raw
            else data_dir / "transfer-tmp"
        ),
        allowed_send_roots=[
            _path(item, "transfer.allowed_send_roots[]", require_absolute=True)
            for item in _string_list(
                transfer_raw.get("allowed_send_roots"),
                "transfer.allowed_send_roots",
                [],
            )
        ],
        require_confirmation=bool(transfer_raw.get("require_confirmation", True)),
        overwrite_existing=bool(transfer_raw.get("overwrite_existing", False)),
    )

    tasks = [
        _load_task(task_raw, index)
        for index, task_raw in enumerate(_as_list(root.get("tasks"), "tasks"))
    ]
    _assert_writable_paths_outside_sources(
        tasks,
        [
            data_dir,
            temp_dir,
            outbox_dir,
            restore.rollback_dir,
            transfer.temp_dir,
            transfer.inbox_dir,
        ],
    )
    enabled_server_count = sum(server.enabled for server in servers)
    configured_required_copies = int(
        backup_raw.get("required_copies", enabled_server_count)
    )
    required_copies = (
        configured_required_copies if enabled_server_count else 0
    )
    if enabled_server_count and (
        required_copies < 1 or required_copies > enabled_server_count
    ):
        raise ValueError(
            f"backup.required_copies must be between 1 and {enabled_server_count}"
        )

    return AppConfig(
        client=ClientSection(
            machine_id=machine_id,
            display_name=str(client_raw.get("display_name") or machine_id),
            timezone=str(client_raw.get("timezone", "Asia/Shanghai")),
            data_dir=data_dir,
            temp_dir=temp_dir,
            outbox_dir=outbox_dir,
        ),
        servers=servers,
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
            required_copies=required_copies,
            keep_local_until_all_uploaded=bool(
                backup_raw.get("keep_local_until_all_uploaded", True)
            ),
        ),
        restore=restore,
        transfer=transfer,
        tasks=tasks,
    )
