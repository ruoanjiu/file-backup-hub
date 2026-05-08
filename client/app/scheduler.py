from __future__ import annotations

import threading
from collections.abc import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from client.app.backup import run_backup_for_strategy
from client.app.config import AppConfig
from client.app.local_db import LocalDb
from client.app.uploader import BackupServerClient


class BackupTaskScheduler:
    def __init__(self, config: AppConfig, log: Callable[[str], None] | None = None) -> None:
        self.config = config
        self.log = log or (lambda message: None)
        self.scheduler = BackgroundScheduler(timezone=config.client.timezone)
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self.scheduler.running:
                return
            db = LocalDb(self.config.client.data_dir / "client.sqlite")
            client = BackupServerClient(self.config.server)
            for strategy in self.config.scheduled_strategies():
                hour, minute = _parse_schedule_time(strategy.schedule_time)
                self.scheduler.add_job(
                    run_backup_for_strategy,
                    trigger="cron",
                    hour=hour,
                    minute=minute,
                    args=[self.config, strategy, client, db],
                    id=f"backup:{strategy.name}",
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                )
                self.log(f"Scheduled {strategy.name} at {strategy.schedule_time}")
            self.scheduler.start()

    def stop(self) -> None:
        with self._lock:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
                self.log("Scheduler stopped")


def _parse_schedule_time(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid schedule_time, expected HH:MM: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Invalid schedule_time, expected HH:MM: {value}")
    return hour, minute
