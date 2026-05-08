from __future__ import annotations

import sqlite3
from pathlib import Path


class LocalDb:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS backup_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    backup_id TEXT NOT NULL UNIQUE,
                    machine_id TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    file_count INTEGER,
                    total_size INTEGER,
                    bundle_sha256 TEXT,
                    error_message TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS restore_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    restore_id TEXT NOT NULL UNIQUE,
                    backup_id TEXT NOT NULL,
                    machine_id TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    rollback_dir TEXT,
                    error_message TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS restored_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    restore_id TEXT NOT NULL,
                    original_path TEXT NOT NULL,
                    restored_path TEXT NOT NULL,
                    rollback_path TEXT,
                    sha256_before TEXT,
                    sha256_after TEXT,
                    status TEXT NOT NULL
                )
                """
            )

    def start_backup_job(
        self,
        backup_id: str,
        machine_id: str,
        strategy_name: str,
        started_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO backup_jobs (
                    backup_id, machine_id, strategy_name, status, started_at
                ) VALUES (?, ?, ?, 'RUNNING', ?)
                """,
                (backup_id, machine_id, strategy_name, started_at),
            )

    def finish_backup_job(
        self,
        backup_id: str,
        status: str,
        finished_at: str,
        file_count: int | None = None,
        total_size: int | None = None,
        bundle_sha256: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE backup_jobs
                SET status = ?,
                    finished_at = ?,
                    file_count = ?,
                    total_size = ?,
                    bundle_sha256 = ?,
                    error_message = ?
                WHERE backup_id = ?
                """,
                (
                    status,
                    finished_at,
                    file_count,
                    total_size,
                    bundle_sha256,
                    error_message,
                    backup_id,
                ),
            )

    def start_restore_job(
        self,
        restore_id: str,
        backup_id: str,
        machine_id: str,
        strategy_name: str,
        started_at: str,
        rollback_dir: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO restore_jobs (
                    restore_id, backup_id, machine_id, strategy_name, status, started_at, rollback_dir
                ) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?)
                """,
                (restore_id, backup_id, machine_id, strategy_name, started_at, rollback_dir),
            )

    def finish_restore_job(
        self,
        restore_id: str,
        status: str,
        finished_at: str,
        error_message: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE restore_jobs
                SET status = ?,
                    finished_at = ?,
                    error_message = ?
                WHERE restore_id = ?
                """,
                (status, finished_at, error_message, restore_id),
            )

    def add_restored_file(
        self,
        restore_id: str,
        original_path: str,
        restored_path: str,
        rollback_path: str | None,
        sha256_before: str | None,
        sha256_after: str | None,
        status: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO restored_files (
                    restore_id, original_path, restored_path, rollback_path,
                    sha256_before, sha256_after, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    restore_id,
                    original_path,
                    restored_path,
                    rollback_path,
                    sha256_before,
                    sha256_after,
                    status,
                ),
            )

    def get_restore_job(self, restore_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT restore_id, backup_id, machine_id, strategy_name, status,
                       started_at, finished_at, rollback_dir, error_message
                FROM restore_jobs
                WHERE restore_id = ?
                """,
                (restore_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_restore_jobs(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT restore_id, backup_id, machine_id, strategy_name, status,
                       started_at, finished_at, rollback_dir, error_message
                FROM restore_jobs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
