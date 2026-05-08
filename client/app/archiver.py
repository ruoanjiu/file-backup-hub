from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

from client.app.manifest import ManifestFileEntry, infer_file_type
from client.app.scanner import ScannedFile
from client.app.utils.hashing import calculate_sha256


def copy_files_to_workdir(files: list[ScannedFile], workdir: Path) -> list[ManifestFileEntry]:
    files_dir = workdir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    entries: list[ManifestFileEntry] = []

    for index, scanned in enumerate(files, start=1):
        file_id = f"{index:06d}"
        suffix = scanned.path.suffix
        backup_name = f"{file_id}{suffix}"
        backup_rel = Path("files") / backup_name
        target = workdir / backup_rel
        temp_target = target.with_name(target.name + ".copying")

        shutil.copy2(scanned.path, temp_target)
        temp_target.replace(target)

        stat = target.stat()
        entries.append(
            ManifestFileEntry(
                file_id=file_id,
                original_path=str(scanned.path),
                backup_path=backup_rel.as_posix(),
                file_name=scanned.path.name,
                file_type=infer_file_type(scanned.path),
                size=stat.st_size,
                mtime=scanned.path.stat().st_mtime,
                sha256=calculate_sha256(target),
                possibly_active=scanned.possibly_active,
            )
        )
    return entries


def write_manifest(workdir: Path, manifest: dict) -> Path:
    path = workdir / "manifest.json"
    temp_path = path.with_suffix(".json.tmp")
    with temp_path.open("w", encoding="utf-8") as file_obj:
        json.dump(manifest, file_obj, ensure_ascii=False, indent=2, sort_keys=True)
        file_obj.write("\n")
    temp_path.replace(path)
    return path


def create_bundle(workdir: Path, output_path: Path) -> str:
    manifest_path = workdir / "manifest.json"
    files_dir = workdir / "files"
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found in workdir: {workdir}")
    if not files_dir.exists():
        raise FileNotFoundError(f"files directory not found in workdir: {workdir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.name + ".creating")
    with tarfile.open(temp_path, mode="w:gz") as tar:
        tar.add(manifest_path, arcname="manifest.json")
        for path in sorted(files_dir.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_file():
                tar.add(path, arcname=path.relative_to(workdir).as_posix())
    temp_path.replace(output_path)
    return calculate_sha256(output_path)
