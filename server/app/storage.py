from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoragePaths:
    storage_dir: Path
    manifest_dir: Path
    bundle_path: Path
    manifest_path: Path


def get_storage_paths(
    storage_root: Path,
    manifest_root: Path,
    machine_id: str,
    task_name: str,
    backup_id: str,
) -> StoragePaths:
    storage_dir = storage_root / machine_id / task_name / backup_id
    manifest_dir = manifest_root / machine_id / task_name / backup_id
    return StoragePaths(
        storage_dir=storage_dir,
        manifest_dir=manifest_dir,
        bundle_path=storage_dir / "bundle.tar.gz",
        manifest_path=manifest_dir / "manifest.json",
    )


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2, sort_keys=True)
        file_obj.write("\n")
    temp_path.replace(path)


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


async def save_bundle_stream(
    chunks: AsyncIterator[bytes],
    expected_sha256: str,
    final_path: Path,
    max_bytes: int,
) -> tuple[str, int]:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.with_name(final_path.name + ".uploading")
    digest = hashlib.sha256()
    total_bytes = 0

    try:
        with temp_path.open("wb") as file_obj:
            async for chunk in chunks:
                if not chunk:
                    continue
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise ValueError("Upload exceeds MAX_UPLOAD_SIZE_MB")
                digest.update(chunk)
                file_obj.write(chunk)

        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError("Bundle SHA256 does not match initialized backup metadata")

        temp_path.replace(final_path)
        return actual_sha256, total_bytes
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise
