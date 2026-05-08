from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass
from pathlib import Path

from client.app.config import BackupSection, TaskConfig, TaskRootConfig


@dataclass(frozen=True)
class ScannedFile:
    path: Path
    root: Path
    possibly_active: bool = False


def _matches(path: Path, root: Path, patterns: list[str]) -> bool:
    base = root if root.is_dir() else root.parent
    rel = path.relative_to(base).as_posix()
    name = path.name
    return any(fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern) for pattern in patterns)


def _iter_files(root: TaskRootConfig) -> list[Path]:
    if not root.path.exists():
        raise FileNotFoundError(f"Backup source does not exist: {root.path}")
    if root.path.is_file():
        return [root.path]
    if not root.path.is_dir():
        raise NotADirectoryError(f"Backup source is not a file or directory: {root.path}")

    iterator = root.path.rglob("*") if root.recursive else root.path.iterdir()
    files: list[Path] = []
    for path in iterator:
        try:
            if path.is_file() and not path.is_symlink():
                files.append(path)
        except OSError:
            continue
    return files


def _is_possibly_active(path: Path, interval_seconds: float) -> bool:
    try:
        first = path.stat()
        time.sleep(interval_seconds)
        second = path.stat()
    except FileNotFoundError:
        return True
    return first.st_size != second.st_size or first.st_mtime != second.st_mtime


def scan_task_files(task: TaskConfig, backup: BackupSection) -> list[ScannedFile]:
    scanned: list[ScannedFile] = []
    for root in task.roots:
        for path in _iter_files(root):
            if not _matches(path, root.path, root.include):
                continue
            if root.exclude and _matches(path, root.path, root.exclude):
                continue
            possibly_active = (
                _is_possibly_active(path, backup.stability_check_interval_seconds)
                if backup.copy_stability_check
                else False
            )
            base = root.path if root.path.is_dir() else root.path.parent
            scanned.append(
                ScannedFile(
                    path=path.resolve(),
                    root=base.resolve(),
                    possibly_active=possibly_active,
                )
            )
    return sorted(scanned, key=lambda item: str(item.path).lower())
