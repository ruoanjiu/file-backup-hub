from __future__ import annotations

import fnmatch
import json
import os
import shutil
import tarfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from client.app.config import AppConfig, ServerSection
from client.app.local_db import LocalDb
from client.app.manifest import now_for_config
from client.app.uploader import BackupServerClient
from client.app.utils.hashing import calculate_sha256


@dataclass(frozen=True)
class RestoreFilePlan:
    manifest_file: dict[str, Any]
    source_path: Path
    target_path: Path
    rollback_path: Path | None = None
    sha256_before: str | None = None


@dataclass(frozen=True)
class VerifyResult:
    backup_id: str
    file_count: int
    bundle_sha256: str
    server_id: str = "server-1"
    status: str = "SUCCESS"


@dataclass(frozen=True)
class RestoreResult:
    restore_id: str
    backup_id: str
    status: str
    restored_count: int
    rollback_dir: Path
    server_id: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class RollbackResult:
    restore_id: str
    status: str
    rolled_back_count: int
    error_message: str | None = None


@dataclass(frozen=True)
class RollbackSnapshot:
    rollback_dir: Path
    plans: list[RestoreFilePlan] = field(default_factory=list)


@dataclass(frozen=True)
class BackupSource:
    server: ServerSection
    client: BackupServerClient
    metadata: dict[str, Any]
    manifest: dict[str, Any]


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reset_workdir(temp_dir: Path, name: str) -> Path:
    workdir = temp_dir / name
    temp_root = temp_dir.resolve()
    resolved = workdir.resolve()
    if resolved.parent != temp_root:
        raise ValueError(f"Refusing to use restore workdir outside temp_dir: {workdir}")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _safe_member_path(extract_dir: Path, member_name: str) -> Path:
    member_path = PurePosixPath(member_name)
    if member_path.is_absolute() or any(part in {"", ".", ".."} for part in member_path.parts):
        raise ValueError(f"Unsafe archive member path: {member_name}")
    target = (extract_dir / Path(*member_path.parts)).resolve()
    extract_root = extract_dir.resolve()
    if not target.is_relative_to(extract_root):
        raise ValueError(f"Archive member escapes extract dir: {member_name}")
    return target


def safe_extract_bundle(bundle_path: Path, extract_dir: Path) -> None:
    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle_path, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            target = _safe_member_path(extract_dir, member.name)
            if member.issym() or member.islnk():
                raise ValueError(f"Archive links are not allowed: {member.name}")
            if not member.isfile() and not member.isdir():
                raise ValueError(f"Archive member type is not allowed: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"Unable to read archive member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)


def verify_extracted_backup(extract_dir: Path, server_manifest: dict[str, Any]) -> int:
    manifest_path = extract_dir / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("bundle does not contain manifest.json")
    bundle_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if _canonical_json(bundle_manifest) != _canonical_json(server_manifest):
        raise ValueError("Server manifest and bundle manifest do not match")

    verified = 0
    for file_entry in server_manifest.get("files", []):
        backup_path = file_entry.get("backup_path")
        expected_sha256 = file_entry.get("sha256")
        if not isinstance(backup_path, str) or not isinstance(expected_sha256, str):
            raise ValueError("manifest file entry is missing backup_path or sha256")
        safe_path = _safe_member_path(extract_dir, backup_path)
        if not safe_path.exists() or not safe_path.is_file():
            raise ValueError(f"backup file is missing from bundle: {backup_path}")
        actual_sha256 = calculate_sha256(safe_path)
        if actual_sha256 != expected_sha256:
            raise ValueError(f"backup file SHA256 mismatch: {backup_path}")
        verified += 1
    return verified


def verify_backup_bundle(
    bundle_path: Path,
    server_manifest: dict[str, Any],
    expected_bundle_sha256: str | None,
    extract_dir: Path,
    server_id: str = "server-1",
) -> VerifyResult:
    actual_bundle_sha256 = calculate_sha256(bundle_path)
    if expected_bundle_sha256 and actual_bundle_sha256 != expected_bundle_sha256:
        raise ValueError("Downloaded bundle SHA256 does not match Server metadata")
    safe_extract_bundle(bundle_path, extract_dir)
    verified_count = verify_extracted_backup(extract_dir, server_manifest)
    return VerifyResult(
        backup_id=server_manifest["backup_id"],
        file_count=verified_count,
        bundle_sha256=actual_bundle_sha256,
        server_id=server_id,
    )


def _load_backup_sources(
    config: AppConfig,
    backup_id: str,
    *,
    server_id: str | None,
    server_client: BackupServerClient | None,
    server_clients: dict[str, BackupServerClient] | None = None,
) -> list[BackupSource]:
    if server_client is not None:
        servers_and_clients = [(config.server, server_client)]
    else:
        servers = (
            [config.get_server(server_id)]
            if server_id and server_id != "auto"
            else config.enabled_servers()
        )
        servers_and_clients = [
            (
                server,
                (server_clients or {}).get(server.id) or BackupServerClient(server),
            )
            for server in servers
        ]

    sources: list[BackupSource] = []
    errors: list[str] = []
    for server, client in servers_and_clients:
        try:
            metadata = client.get_backup_metadata(backup_id)
            manifest = client.download_manifest(backup_id)
            sources.append(
                BackupSource(
                    server=server,
                    client=client,
                    metadata=metadata,
                    manifest=manifest,
                )
            )
        except Exception as exc:
            errors.append(f"{server.id}: {exc}")

    if not sources:
        raise RuntimeError(
            f"Backup {backup_id} is unavailable on the selected Server(s): "
            + "; ".join(errors)
        )

    if not server_id or server_id == "auto":
        hashes = {
            source.metadata.get("bundle_sha256")
            for source in sources
            if source.metadata.get("bundle_sha256")
        }
        manifests = {_canonical_json(source.manifest) for source in sources}
        if len(hashes) > 1 or len(manifests) > 1:
            raise ValueError(
                f"Backup {backup_id} has conflicting copies across Servers; choose and verify one Server explicitly"
            )
    return sources


def _matches_includes(file_entry: dict[str, Any], includes: list[str] | None) -> bool:
    if not includes:
        return True
    candidates = [
        str(file_entry.get("file_name", "")),
        str(file_entry.get("backup_path", "")),
        str(file_entry.get("original_path", "")),
    ]
    return any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates for pattern in includes)


def _resolve_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _assert_allowed_target(target_path: Path, allowed_roots: list[Path]) -> None:
    if not allowed_roots:
        raise ValueError("restore.allowed_roots must contain at least one path")
    resolved_target = _resolve_path(target_path)
    for root in allowed_roots:
        resolved_root = _resolve_path(root)
        if resolved_target == resolved_root or resolved_target.is_relative_to(resolved_root):
            return
    raise ValueError(f"Restore target is outside allowed_roots: {target_path}")


def parse_path_maps(path_maps: list[str] | None) -> list[tuple[Path, Path]]:
    parsed: list[tuple[Path, Path]] = []
    for item in path_maps or []:
        source, separator, target = item.partition("=")
        if not separator or not source.strip() or not target.strip():
            raise ValueError(f"Invalid path-map, expected OLD=NEW: {item}")
        parsed.append((Path(source.strip()), Path(target.strip())))
    return parsed


def apply_path_maps(original_path: Path, path_maps: list[tuple[Path, Path]]) -> Path:
    original = _resolve_path(original_path)
    for source_root, target_root in path_maps:
        resolved_source = _resolve_path(source_root)
        if original == resolved_source or original.is_relative_to(resolved_source):
            relative = original.relative_to(resolved_source)
            return _resolve_path(target_root / relative)
    return original


def build_restore_plan(
    server_manifest: dict[str, Any],
    extract_dir: Path,
    allowed_roots: list[Path],
    path_maps: list[tuple[Path, Path]] | None = None,
    includes: list[str] | None = None,
) -> list[RestoreFilePlan]:
    plans: list[RestoreFilePlan] = []
    for file_entry in server_manifest.get("files", []):
        if not _matches_includes(file_entry, includes):
            continue
        original_path_raw = file_entry.get("original_path")
        backup_path_raw = file_entry.get("backup_path")
        if not isinstance(original_path_raw, str) or not isinstance(backup_path_raw, str):
            raise ValueError("manifest file entry is missing original_path or backup_path")
        source_path = _safe_member_path(extract_dir, backup_path_raw)
        target_path = apply_path_maps(Path(original_path_raw), path_maps or [])
        _assert_allowed_target(target_path, allowed_roots)
        plans.append(
            RestoreFilePlan(
                manifest_file=file_entry,
                source_path=source_path,
                target_path=target_path,
            )
        )
    if not plans:
        raise ValueError("No files selected for restore")
    return plans


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(destination.name + ".tmp")
    shutil.copy2(source, temp_path)
    temp_path.replace(destination)


def create_rollback_snapshot(
    plans: list[RestoreFilePlan],
    rollback_root: Path,
    restore_id: str,
) -> RollbackSnapshot:
    rollback_dir = rollback_root / restore_id
    files_dir = rollback_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    manifest_entries: list[dict[str, Any]] = []
    updated_plans: list[RestoreFilePlan] = []

    for plan in plans:
        target = plan.target_path
        rollback_path: Path | None = None
        sha256_before: str | None = None
        existed_before = target.exists()
        if existed_before:
            rollback_path = files_dir / f"{plan.manifest_file['file_id']}.current"
            _copy_atomic(target, rollback_path)
            sha256_before = calculate_sha256(rollback_path)
        manifest_entries.append(
            {
                "file_id": plan.manifest_file["file_id"],
                "original_path": plan.manifest_file["original_path"],
                "target_path": str(target),
                "existed_before_restore": existed_before,
                "rollback_path": str(rollback_path) if rollback_path else None,
                "sha256_before": sha256_before,
            }
        )
        updated_plans.append(
            RestoreFilePlan(
                manifest_file=plan.manifest_file,
                source_path=plan.source_path,
                target_path=plan.target_path,
                rollback_path=rollback_path,
                sha256_before=sha256_before,
            )
        )

    manifest_path = rollback_dir / "manifest_before_restore.json"
    temp_manifest = manifest_path.with_suffix(".json.tmp")
    with temp_manifest.open("w", encoding="utf-8") as file_obj:
        json.dump(
            {
                "restore_id": restore_id,
                "created_at": datetime.now().isoformat(),
                "files": manifest_entries,
            },
            file_obj,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        file_obj.write("\n")
    temp_manifest.replace(manifest_path)
    return RollbackSnapshot(rollback_dir=rollback_dir, plans=updated_plans)


def atomic_restore_file(source: Path, destination: Path, expected_sha256: str) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(destination.name + ".restore_tmp")
    shutil.copy2(source, temp_path)
    actual_sha256 = calculate_sha256(temp_path)
    if actual_sha256 != expected_sha256:
        temp_path.unlink(missing_ok=True)
        raise ValueError(f"Restored temp file SHA256 mismatch: {destination}")
    os.replace(temp_path, destination)
    return calculate_sha256(destination)


def _new_restore_id(config: AppConfig) -> str:
    timestamp = now_for_config(config).strftime("%Y%m%d_%H%M%S")
    return f"restore_{timestamp}_{uuid.uuid4().hex[:8]}"


def run_verify(
    config: AppConfig,
    backup_id: str,
    server_client: BackupServerClient | None = None,
    server_id: str | None = None,
    server_clients: dict[str, BackupServerClient] | None = None,
) -> VerifyResult:
    sources = _load_backup_sources(
        config,
        backup_id,
        server_id=server_id,
        server_client=server_client,
        server_clients=server_clients,
    )
    errors: list[str] = []
    for source in sources:
        workdir = _reset_workdir(
            config.client.temp_dir,
            f"verify_{backup_id}_{source.server.id}_{uuid.uuid4().hex[:8]}",
        )
        try:
            bundle_path = source.client.download_bundle(backup_id, workdir / "bundle.tar.gz")
            return verify_backup_bundle(
                bundle_path,
                source.manifest,
                source.metadata.get("bundle_sha256"),
                workdir / "extracted",
                source.server.id,
            )
        except Exception as exc:
            errors.append(f"{source.server.id}: {exc}")
        finally:
            if workdir.exists():
                shutil.rmtree(workdir)
    raise RuntimeError("All selected backup copies failed verification: " + "; ".join(errors))


def run_restore(
    config: AppConfig,
    backup_id: str,
    server_client: BackupServerClient | None = None,
    local_db: LocalDb | None = None,
    path_maps: list[str] | None = None,
    includes: list[str] | None = None,
    allow_cross_machine: bool = False,
    server_id: str | None = None,
    server_clients: dict[str, BackupServerClient] | None = None,
) -> RestoreResult:
    db = local_db or LocalDb(config.client.data_dir / "client.sqlite")
    restore_id = _new_restore_id(config)
    workdir = _reset_workdir(config.client.temp_dir, f"{restore_id}_work")
    rollback_dir = config.restore.rollback_dir / restore_id
    selected_server_id: str | None = None
    try:
        sources = _load_backup_sources(
            config,
            backup_id,
            server_id=server_id,
            server_client=server_client,
            server_clients=server_clients,
        )
        server_manifest: dict[str, Any] | None = None
        extract_dir: Path | None = None
        download_errors: list[str] = []
        for source in sources:
            try:
                if workdir.exists():
                    shutil.rmtree(workdir)
                workdir.mkdir(parents=True, exist_ok=True)
                bundle_path = source.client.download_bundle(backup_id, workdir / "bundle.tar.gz")
                candidate_extract_dir = workdir / "extracted"
                verify_backup_bundle(
                    bundle_path,
                    source.manifest,
                    source.metadata.get("bundle_sha256"),
                    candidate_extract_dir,
                    source.server.id,
                )
                selected_server_id = source.server.id
                server_manifest = source.manifest
                extract_dir = candidate_extract_dir
                break
            except Exception as exc:
                download_errors.append(f"{source.server.id}: {exc}")
        if server_manifest is None or extract_dir is None:
            raise RuntimeError(
                "All selected backup copies failed verification: " + "; ".join(download_errors)
            )
        if (
            config.restore.require_same_machine_id
            and not allow_cross_machine
            and server_manifest.get("machine_id") != config.client.machine_id
        ):
            raise ValueError("Backup machine_id does not match this client")
        plans = build_restore_plan(
            server_manifest,
            extract_dir,
            config.restore.allowed_roots,
            parse_path_maps(path_maps),
            includes,
        )
        snapshot = create_rollback_snapshot(plans, config.restore.rollback_dir, restore_id)
        db.start_restore_job(
            restore_id,
            backup_id,
            str(server_manifest["machine_id"]),
            str(server_manifest["task_name"]),
            now_for_config(config).isoformat(),
            str(snapshot.rollback_dir),
            selected_server_id,
        )
        restored_count = 0
        for plan in snapshot.plans:
            expected_sha256 = str(plan.manifest_file["sha256"])
            sha256_after = atomic_restore_file(plan.source_path, plan.target_path, expected_sha256)
            db.add_restored_file(
                restore_id,
                str(plan.manifest_file["original_path"]),
                str(plan.target_path),
                str(plan.rollback_path) if plan.rollback_path else None,
                plan.sha256_before,
                sha256_after,
                "RESTORED",
            )
            restored_count += 1
        db.finish_restore_job(restore_id, "SUCCESS", now_for_config(config).isoformat())
        return RestoreResult(
            restore_id=restore_id,
            backup_id=backup_id,
            status="SUCCESS",
            restored_count=restored_count,
            rollback_dir=snapshot.rollback_dir,
            server_id=selected_server_id,
        )
    except Exception as exc:
        if db.get_restore_job(restore_id) is None:
            db.start_restore_job(
                restore_id,
                backup_id,
                str(backup_id),
                "unknown",
                now_for_config(config).isoformat(),
                str(rollback_dir),
                selected_server_id or server_id,
            )
        db.finish_restore_job(restore_id, "FAILED", now_for_config(config).isoformat(), str(exc))
        return RestoreResult(
            restore_id=restore_id,
            backup_id=backup_id,
            status="FAILED",
            restored_count=0,
            rollback_dir=rollback_dir,
            server_id=selected_server_id or server_id,
            error_message=str(exc),
        )
    finally:
        if workdir.exists():
            shutil.rmtree(workdir)


def rollback_restore(
    config: AppConfig,
    restore_id: str,
    local_db: LocalDb | None = None,
) -> RollbackResult:
    db = local_db or LocalDb(config.client.data_dir / "client.sqlite")
    job = db.get_restore_job(restore_id)
    rollback_dir = Path(job["rollback_dir"]) if job else config.restore.rollback_dir / restore_id
    manifest_path = rollback_dir / "manifest_before_restore.json"
    if not manifest_path.exists():
        return RollbackResult(restore_id=restore_id, status="FAILED", rolled_back_count=0, error_message="Rollback manifest not found")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rolled_back = 0
        for entry in manifest.get("files", []):
            target = Path(entry["target_path"])
            _assert_allowed_target(target, config.restore.allowed_roots)
            if entry.get("existed_before_restore"):
                rollback_path = Path(entry["rollback_path"])
                if not rollback_path.exists():
                    raise ValueError(f"Rollback file is missing: {rollback_path}")
                expected_sha256 = entry.get("sha256_before")
                if expected_sha256 and calculate_sha256(rollback_path) != expected_sha256:
                    raise ValueError(f"Rollback file SHA256 mismatch: {rollback_path}")
                _copy_atomic(rollback_path, target)
            elif target.exists():
                target.unlink()
            rolled_back += 1
        return RollbackResult(restore_id=restore_id, status="SUCCESS", rolled_back_count=rolled_back)
    except Exception as exc:
        return RollbackResult(
            restore_id=restore_id,
            status="FAILED",
            rolled_back_count=0,
            error_message=str(exc),
        )
