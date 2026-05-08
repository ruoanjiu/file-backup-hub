from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from client.app.archiver import copy_files_to_workdir, create_bundle, write_manifest
from client.app.config import AppConfig, TaskConfig
from client.app.local_db import LocalDb
from client.app.manifest import build_manifest, generate_backup_id, now_for_config
from client.app.scanner import scan_task_files
from client.app.uploader import BackupServerClient


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
    db.start_backup_job(backup_id, config.client.machine_id, task.name, created_at.isoformat())

    prepared: PreparedBackup | None = None
    try:
        prepared = prepare_backup_with_id(config, task, backup_id, created_at)
        uploader = server_client or BackupServerClient(config.server)
        uploader.upload_backup(prepared.manifest, prepared.bundle_path, prepared.bundle_sha256)
        finished_at = now_for_config(config).isoformat()
        db.finish_backup_job(
            backup_id,
            "SUCCESS",
            finished_at,
            file_count=prepared.file_count,
            total_size=prepared.total_size,
            bundle_sha256=prepared.bundle_sha256,
        )
        return BackupResult(
            backup_id=backup_id,
            task_name=task.name,
            status="SUCCESS",
            file_count=prepared.file_count,
            total_size=prepared.total_size,
            bundle_sha256=prepared.bundle_sha256,
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
        )
    finally:
        if cleanup and prepared is not None and prepared.workdir.exists():
            _remove_workdir(config.client.temp_dir, prepared.workdir)


def prepare_backup_with_id(
    config: AppConfig,
    task: TaskConfig,
    backup_id: str,
    created_at: datetime,
) -> PreparedBackup:
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
