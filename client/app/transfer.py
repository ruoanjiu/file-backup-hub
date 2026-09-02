from __future__ import annotations

import json
import os
import shutil
import tarfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from client.app.config import AppConfig
from client.app.manifest import now_for_config
from client.app.restore import safe_extract_bundle
from client.app.uploader import BackupServerClient
from client.app.utils.hashing import calculate_sha256


@dataclass(frozen=True)
class PreparedTransfer:
    transfer_id: str
    receiver_device_id: str
    workdir: Path
    bundle_path: Path
    manifest: dict[str, Any]
    bundle_sha256: str
    file_count: int
    total_size: int


@dataclass(frozen=True)
class SendTransferResult:
    transfer_id: str
    server_id: str | None
    status: str
    file_count: int
    total_size: int
    error_message: str | None = None
    workdir: Path | None = None


@dataclass(frozen=True)
class ReceiveTransferResult:
    transfer_id: str
    server_id: str
    status: str
    received_count: int
    destination: Path
    error_message: str | None = None


def _reset_transfer_workdir(root: Path, transfer_id: str) -> Path:
    resolved_root = root.expanduser().resolve(strict=False)
    workdir = (resolved_root / transfer_id).resolve(strict=False)
    if workdir.parent != resolved_root:
        raise ValueError(f"Unsafe transfer workdir: {workdir}")
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _assert_send_path_allowed(config: AppConfig, path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if not config.transfer.allowed_send_roots:
        return resolved
    for root in config.transfer.allowed_send_roots:
        allowed = root.expanduser().resolve(strict=False)
        if resolved == allowed or resolved.is_relative_to(allowed):
            return resolved
    raise ValueError(f"Selected send path is outside transfer.allowed_send_roots: {path}")


def _iter_selected_files(paths: list[Path]) -> list[tuple[Path, PurePosixPath]]:
    if not paths:
        raise ValueError("Select at least one file or folder to send")
    entries: list[tuple[Path, PurePosixPath]] = []
    used_top_names: set[str] = set()
    for selected in paths:
        if selected.is_symlink():
            raise ValueError(f"Symbolic links cannot be sent: {selected}")
        top_name = selected.name
        if top_name in used_top_names:
            raise ValueError(f"Two selected items have the same top-level name: {top_name}")
        used_top_names.add(top_name)
        if selected.is_file():
            entries.append((selected, PurePosixPath(top_name)))
            continue
        if not selected.is_dir():
            raise ValueError(f"Send source is not a regular file or folder: {selected}")
        for path in sorted(selected.rglob("*"), key=lambda item: item.as_posix().lower()):
            if path.is_symlink():
                continue
            if path.is_file():
                entries.append(
                    (
                        path,
                        PurePosixPath(top_name) / PurePosixPath(path.relative_to(selected).as_posix()),
                    )
                )
    if not entries:
        raise ValueError("No regular files were selected for transfer")
    return entries


def prepare_transfer(
    config: AppConfig,
    selected_paths: list[Path],
    receiver_device_id: str,
    *,
    created_at: datetime | None = None,
) -> PreparedTransfer:
    created_at = created_at or now_for_config(config)
    transfer_id = f"transfer_{created_at.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    validated = [_assert_send_path_allowed(config, path) for path in selected_paths]
    files = _iter_selected_files(validated)
    workdir = _reset_transfer_workdir(config.transfer.temp_dir, transfer_id)
    files_dir = workdir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    manifest_files: list[dict[str, Any]] = []
    total_size = 0

    for index, (source, relative_path) in enumerate(files, start=1):
        backup_path = PurePosixPath("files") / relative_path
        destination = workdir / Path(*backup_path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_destination = destination.with_name(destination.name + ".copying")
        shutil.copy2(source, temp_destination)
        temp_destination.replace(destination)
        size = destination.stat().st_size
        total_size += size
        manifest_files.append(
            {
                "file_id": f"{index:06d}",
                "relative_path": relative_path.as_posix(),
                "backup_path": backup_path.as_posix(),
                "file_name": source.name,
                "size": size,
                "sha256": calculate_sha256(destination),
            }
        )

    manifest = {
        "schema_version": "1.0",
        "transfer_id": transfer_id,
        "sender_device_id": config.client.machine_id,
        "receiver_device_id": receiver_device_id,
        "created_at": created_at.isoformat(),
        "file_count": len(manifest_files),
        "total_size": total_size,
        "files": manifest_files,
    }
    manifest_path = workdir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    bundle_path = workdir / "bundle.tar.gz"
    temp_bundle = bundle_path.with_name(bundle_path.name + ".creating")
    with tarfile.open(temp_bundle, "w:gz") as tar:
        tar.add(manifest_path, arcname="manifest.json")
        for path in sorted(files_dir.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(workdir).as_posix())
    temp_bundle.replace(bundle_path)
    return PreparedTransfer(
        transfer_id=transfer_id,
        receiver_device_id=receiver_device_id,
        workdir=workdir,
        bundle_path=bundle_path,
        manifest=manifest,
        bundle_sha256=calculate_sha256(bundle_path),
        file_count=len(manifest_files),
        total_size=total_size,
    )


def send_transfer(
    config: AppConfig,
    selected_paths: list[Path],
    receiver_device_id: str,
    *,
    server_id: str | None = None,
    server_clients: dict[str, BackupServerClient] | None = None,
    cleanup: bool = True,
) -> SendTransferResult:
    prepared = prepare_transfer(config, selected_paths, receiver_device_id)
    servers = (
        [config.get_server(server_id)]
        if server_id and server_id != "auto"
        else config.enabled_servers()
    )
    errors: list[str] = []
    for server in servers:
        try:
            client = (server_clients or {}).get(server.id) or BackupServerClient(server)
            response = client.upload_transfer(
                prepared.manifest,
                prepared.bundle_path,
                prepared.bundle_sha256,
            )
            if response.get("status") not in {"AVAILABLE", "ACCEPTED", "COMPLETED"}:
                raise RuntimeError(f"Unexpected transfer status: {response.get('status')}")
            if cleanup and prepared.workdir.exists():
                shutil.rmtree(prepared.workdir)
            return SendTransferResult(
                transfer_id=prepared.transfer_id,
                server_id=server.id,
                status="AVAILABLE",
                file_count=prepared.file_count,
                total_size=prepared.total_size,
            )
        except Exception as exc:
            errors.append(f"{server.id}: {exc}")
    return SendTransferResult(
        transfer_id=prepared.transfer_id,
        server_id=None,
        status="FAILED",
        file_count=prepared.file_count,
        total_size=prepared.total_size,
        error_message="; ".join(errors),
        workdir=prepared.workdir,
    )


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _collision_safe_top_names(destination: Path, manifest: dict[str, Any]) -> dict[str, str]:
    top_names = {
        PurePosixPath(str(item["relative_path"])).parts[0]
        for item in manifest.get("files", [])
    }
    mapping: dict[str, str] = {}
    for top_name in sorted(top_names):
        candidate = top_name
        index = 1
        while (destination / candidate).exists():
            stem = Path(top_name).stem
            suffix = Path(top_name).suffix
            candidate = f"{stem} ({index}){suffix}"
            index += 1
        mapping[top_name] = candidate
    return mapping


def receive_transfer(
    config: AppConfig,
    transfer_id: str,
    *,
    server_id: str,
    destination: Path | None = None,
    server_client: BackupServerClient | None = None,
) -> ReceiveTransferResult:
    server = config.get_server(server_id)
    client = server_client or BackupServerClient(server)
    destination = (destination or config.transfer.inbox_dir).expanduser().resolve(strict=False)
    workdir = _reset_transfer_workdir(config.transfer.temp_dir, f"receive_{transfer_id}")
    try:
        metadata = client.get_transfer_metadata(transfer_id)
        manifest = client.download_transfer_manifest(transfer_id)
        if manifest.get("receiver_device_id") != config.client.machine_id:
            raise ValueError("Transfer receiver does not match this device")
        if metadata.get("status") == "AVAILABLE":
            client.update_transfer_status(transfer_id, "accept")
        bundle_path = client.download_transfer_bundle(transfer_id, workdir / "bundle.tar.gz")
        actual_bundle_sha256 = calculate_sha256(bundle_path)
        if actual_bundle_sha256 != metadata.get("bundle_sha256"):
            raise ValueError("Transfer bundle SHA256 mismatch")
        extract_dir = workdir / "extracted"
        safe_extract_bundle(bundle_path, extract_dir)
        bundled_manifest = json.loads((extract_dir / "manifest.json").read_text(encoding="utf-8"))
        if _canonical_json(bundled_manifest) != _canonical_json(manifest):
            raise ValueError("Server and bundled transfer manifests do not match")

        destination.mkdir(parents=True, exist_ok=True)
        top_mapping = _collision_safe_top_names(destination, manifest)
        received = 0
        for entry in manifest.get("files", []):
            relative = PurePosixPath(str(entry["relative_path"]))
            backup_path = PurePosixPath(str(entry["backup_path"]))
            if relative.is_absolute() or backup_path.is_absolute():
                raise ValueError("Transfer manifest contains an absolute path")
            if any(part in {"", ".", ".."} for part in (*relative.parts, *backup_path.parts)):
                raise ValueError("Transfer manifest contains an unsafe path")
            source = extract_dir / Path(*backup_path.parts)
            if not source.is_file() or calculate_sha256(source) != entry["sha256"]:
                raise ValueError(f"Transferred file verification failed: {relative}")
            mapped_relative = Path(top_mapping[relative.parts[0]], *relative.parts[1:])
            target = destination / mapped_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            temp_target = target.with_name(target.name + ".receiving")
            shutil.copy2(source, temp_target)
            if calculate_sha256(temp_target) != entry["sha256"]:
                temp_target.unlink(missing_ok=True)
                raise ValueError(f"Received file verification failed: {mapped_relative}")
            os.replace(temp_target, target)
            received += 1
        client.update_transfer_status(transfer_id, "complete")
        return ReceiveTransferResult(
            transfer_id=transfer_id,
            server_id=server_id,
            status="COMPLETED",
            received_count=received,
            destination=destination,
        )
    except Exception as exc:
        return ReceiveTransferResult(
            transfer_id=transfer_id,
            server_id=server_id,
            status="FAILED",
            received_count=0,
            destination=destination,
            error_message=str(exc),
        )
    finally:
        if workdir.exists():
            shutil.rmtree(workdir)


def reject_transfer(
    config: AppConfig,
    transfer_id: str,
    *,
    server_id: str,
    server_client: BackupServerClient | None = None,
) -> dict[str, Any]:
    server = config.get_server(server_id)
    client = server_client or BackupServerClient(server)
    metadata = client.get_transfer_metadata(transfer_id)
    if metadata.get("receiver_device_id") != config.client.machine_id:
        raise ValueError("Transfer receiver does not match this device")
    response = client.update_transfer_status(transfer_id, "reject")
    return {
        **response,
        "source_files_deleted": False,
        "transfer_bundle_deleted": False,
    }
