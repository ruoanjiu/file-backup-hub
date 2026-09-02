from __future__ import annotations

import shutil
import uuid
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from client.app.archiver import copy_files_to_workdir, create_bundle, write_manifest
from client.app.config import AppConfig, TaskConfig
from client.app.local_db import LocalDb
from client.app.manifest import build_manifest, generate_backup_id, now_for_config
from client.app.scanner import scan_task_files
from client.app.uploader import (
    BackupServerClient,
    DestinationUploadResult,
    upload_with_retry,
)
from client.app.utils.hashing import calculate_sha256


@dataclass(frozen=True)
class PreparedBackup:
    backup_id: str
    task_name: str
    workdir: Path
    bundle_path: Path
    manifest: dict
    bundle_sha256: str
    file_count: int
    total_size: int


@dataclass(frozen=True)
class BackupResult:
    backup_id: str
    task_name: str
    status: str
    file_count: int
    total_size: int
    bundle_sha256: str | None = None
    error_message: str | None = None
    destinations: tuple[DestinationUploadResult, ...] = ()
    outbox_path: Path | None = None


def _reset_workdir(temp_dir: Path, backup_id: str) -> Path:
    workdir = temp_dir / backup_id
    temp_root = temp_dir.resolve()
    resolved_workdir = workdir.resolve()
    if resolved_workdir.parent != temp_root:
        raise ValueError(f"Refusing to use workdir outside temp_dir: {workdir}")
    if resolved_workdir.exists():
        shutil.rmtree(resolved_workdir)
    resolved_workdir.mkdir(parents=True, exist_ok=True)
    return resolved_workdir


def _remove_workdir(temp_dir: Path, workdir: Path) -> None:
    temp_root = temp_dir.resolve()
    resolved_workdir = workdir.resolve()
    if resolved_workdir.parent != temp_root:
        raise ValueError(f"Refusing to remove workdir outside temp_dir: {workdir}")
    if resolved_workdir.exists():
        shutil.rmtree(resolved_workdir)


def prepare_backup(
    config: AppConfig,
    task: TaskConfig,
    created_at: datetime | None = None,
) -> PreparedBackup:
    created_at = created_at or now_for_config(config)
    backup_id = generate_backup_id(
        config.client.machine_id,
        task.name,
        created_at,
        uuid.uuid4().hex[:8],
    )
    workdir = _reset_workdir(config.client.temp_dir, backup_id)

    scanned = scan_task_files(task, config.backup)
    if not scanned:
        raise ValueError(f"No files matched task {task.name}")

    manifest_files = copy_files_to_workdir(scanned, workdir)
    manifest = build_manifest(config, task, backup_id, created_at, manifest_files)
    write_manifest(workdir, manifest)

    bundle_path = workdir / "bundle.tar.gz"
    bundle_sha256 = create_bundle(workdir, bundle_path)
    return PreparedBackup(
        backup_id=backup_id,
        task_name=task.name,
        workdir=workdir,
        bundle_path=bundle_path,
        manifest=manifest,
        bundle_sha256=bundle_sha256,
        file_count=manifest["file_count"],
        total_size=manifest["total_size"],
    )


def run_backup_for_task(
    config: AppConfig,
    task: TaskConfig,
    server_client: BackupServerClient | None = None,
    server_clients: dict[str, BackupServerClient] | None = None,
    local_db: LocalDb | None = None,
    cleanup: bool = True,
) -> BackupResult:
    created_at = now_for_config(config)
    backup_id = generate_backup_id(
        config.client.machine_id,
        task.name,
        created_at,
        uuid.uuid4().hex[:8],
    )
    db = local_db or LocalDb(config.client.data_dir / "client.sqlite")
    outbox_workdir = config.client.outbox_dir / backup_id
    db.start_backup_job(
        backup_id,
        config.client.machine_id,
        task.name,
        created_at.isoformat(),
        str(outbox_workdir),
    )

    prepared: PreparedBackup | None = None
    overall_status = "FAILED"
    try:
        prepared = prepare_backup_with_id(
            config,
            task,
            backup_id,
            created_at,
            work_root=config.client.outbox_dir,
        )
        copied_files_dir = prepared.workdir / "files"
        if copied_files_dir.exists():
            shutil.rmtree(copied_files_dir)

        enabled_servers = config.enabled_servers()
        if not enabled_servers:
            raise ValueError("没有已启用的 Server；请先重新配对后再运行备份")
        results: list[DestinationUploadResult] = []
        supplied_clients = dict(server_clients or {})
        if server_client is not None:
            supplied_clients.setdefault(enabled_servers[0].id, server_client)

        for server in enabled_servers:
            db.upsert_backup_destination(
                backup_id,
                server.id,
                server.name,
                server.base_url,
            )

            def client_factory(_: object, *, server_id: str = server.id) -> BackupServerClient:
                return supplied_clients.get(server_id) or BackupServerClient(server)

            result = upload_with_retry(
                server,
                prepared.manifest,
                prepared.bundle_path,
                prepared.bundle_sha256,
                retry_count=config.backup.retry_count,
                retry_interval_seconds=config.backup.retry_interval_seconds,
                client_factory=client_factory,
            )
            db.record_backup_destination_result(
                backup_id,
                result.server_id,
                result.status,
                attempt_count=result.attempts,
                uploaded_at=(now_for_config(config).isoformat() if result.status == "COMPLETED" else None),
                bundle_sha256=result.bundle_sha256,
                error_message=result.error_message,
            )
            results.append(result)

        completed = sum(result.status == "COMPLETED" for result in results)
        if completed >= config.backup.required_copies:
            overall_status = "SUCCESS"
        elif completed:
            overall_status = "DEGRADED"
        else:
            overall_status = "FAILED"
        errors = [
            f"{result.server_id}: {result.error_message}"
            for result in results
            if result.error_message
        ]
        finished_at = now_for_config(config).isoformat()
        db.finish_backup_job(
            backup_id,
            overall_status,
            finished_at,
            file_count=prepared.file_count,
            total_size=prepared.total_size,
            bundle_sha256=prepared.bundle_sha256,
            error_message="; ".join(errors) or None,
        )
        return BackupResult(
            backup_id=backup_id,
            task_name=task.name,
            status=overall_status,
            file_count=prepared.file_count,
            total_size=prepared.total_size,
            bundle_sha256=prepared.bundle_sha256,
            error_message="; ".join(errors) or None,
            destinations=tuple(results),
            outbox_path=(prepared.workdir if overall_status != "SUCCESS" else None),
        )
    except Exception as exc:
        db.finish_backup_job(
            backup_id,
            "FAILED",
            now_for_config(config).isoformat(),
            error_message=str(exc),
        )
        return BackupResult(
            backup_id=backup_id,
            task_name=task.name,
            status="FAILED",
            file_count=0,
            total_size=0,
            error_message=str(exc),
            outbox_path=prepared.workdir if prepared is not None else None,
        )
    finally:
        if (
            cleanup
            and overall_status == "SUCCESS"
            and prepared is not None
            and prepared.workdir.exists()
        ):
            _remove_workdir(config.client.outbox_dir, prepared.workdir)


def prepare_backup_with_id(
    config: AppConfig,
    task: TaskConfig,
    backup_id: str,
    created_at: datetime,
    work_root: Path | None = None,
) -> PreparedBackup:
    workdir = _reset_workdir(work_root or config.client.temp_dir, backup_id)

    scanned = scan_task_files(task, config.backup)
    if not scanned:
        raise ValueError(f"No files matched task {task.name}")

    manifest_files = copy_files_to_workdir(scanned, workdir)
    manifest = build_manifest(config, task, backup_id, created_at, manifest_files)
    write_manifest(workdir, manifest)
    bundle_path = workdir / "bundle.tar.gz"
    bundle_sha256 = create_bundle(workdir, bundle_path)
    return PreparedBackup(
        backup_id=backup_id,
        task_name=task.name,
        workdir=workdir,
        bundle_path=bundle_path,
        manifest=manifest,
        bundle_sha256=bundle_sha256,
        file_count=manifest["file_count"],
        total_size=manifest["total_size"],
    )


def retry_backup_destinations(
    config: AppConfig,
    backup_id: str,
    *,
    local_db: LocalDb | None = None,
    server_clients: dict[str, BackupServerClient] | None = None,
    cleanup: bool = True,
) -> BackupResult:
    db = local_db or LocalDb(config.client.data_dir / "client.sqlite")
    job = db.get_backup_job(backup_id)
    if job is None:
        raise ValueError(f"Local backup job not found: {backup_id}")
    workdir = Path(job.get("workdir") or (config.client.outbox_dir / backup_id))
    manifest_path = workdir / "manifest.json"
    bundle_path = workdir / "bundle.tar.gz"
    if not manifest_path.exists() or not bundle_path.exists():
        raise FileNotFoundError(f"Outbox data is incomplete for backup {backup_id}: {workdir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_sha256 = calculate_sha256(bundle_path)
    if job.get("bundle_sha256") and bundle_sha256 != job["bundle_sha256"]:
        raise ValueError(f"Outbox bundle SHA256 mismatch for backup {backup_id}")

    previous = {
        item["server_id"]: item
        for item in db.list_backup_destinations(backup_id)
    }
    supplied_clients = dict(server_clients or {})
    results: list[DestinationUploadResult] = []
    enabled_servers = config.enabled_servers()
    if not enabled_servers:
        raise ValueError("没有已启用的 Server；无法补传 outbox")
    for server in enabled_servers:
        existing = previous.get(server.id)
        if existing and existing["status"] == "COMPLETED":
            results.append(
                DestinationUploadResult(
                    server_id=server.id,
                    server_name=server.name,
                    base_url=server.base_url,
                    status="COMPLETED",
                    attempts=int(existing["attempt_count"] or 0),
                    bundle_sha256=existing.get("bundle_sha256") or bundle_sha256,
                )
            )
            continue
        db.upsert_backup_destination(
            backup_id,
            server.id,
            server.name,
            server.base_url,
        )

        def client_factory(_: object, *, server_id: str = server.id) -> BackupServerClient:
            return supplied_clients.get(server_id) or BackupServerClient(server)

        result = upload_with_retry(
            server,
            manifest,
            bundle_path,
            bundle_sha256,
            retry_count=config.backup.retry_count,
            retry_interval_seconds=config.backup.retry_interval_seconds,
            client_factory=client_factory,
        )
        db.record_backup_destination_result(
            backup_id,
            server.id,
            result.status,
            attempt_count=result.attempts,
            uploaded_at=(now_for_config(config).isoformat() if result.status == "COMPLETED" else None),
            bundle_sha256=result.bundle_sha256,
            error_message=result.error_message,
        )
        results.append(result)

    completed = sum(result.status == "COMPLETED" for result in results)
    status = (
        "SUCCESS"
        if completed >= config.backup.required_copies
        else "DEGRADED" if completed else "FAILED"
    )
    errors = [
        f"{result.server_id}: {result.error_message}"
        for result in results
        if result.error_message
    ]
    db.finish_backup_job(
        backup_id,
        status,
        now_for_config(config).isoformat(),
        file_count=int(job.get("file_count") or manifest.get("file_count") or 0),
        total_size=int(job.get("total_size") or manifest.get("total_size") or 0),
        bundle_sha256=bundle_sha256,
        error_message="; ".join(errors) or None,
    )
    if cleanup and status == "SUCCESS" and workdir.exists():
        _remove_workdir(config.client.outbox_dir, workdir)
    return BackupResult(
        backup_id=backup_id,
        task_name=str(job["task_name"]),
        status=status,
        file_count=int(job.get("file_count") or manifest.get("file_count") or 0),
        total_size=int(job.get("total_size") or manifest.get("total_size") or 0),
        bundle_sha256=bundle_sha256,
        error_message="; ".join(errors) or None,
        destinations=tuple(results),
        outbox_path=(workdir if status != "SUCCESS" else None),
    )
