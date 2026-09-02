from __future__ import annotations

import logging
import signal
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from client.app.backup import retry_backup_destinations
from client.app.config import AppConfig, load_config
from client.app.local_db import LocalDb
from client.app.scheduler import BackupTaskScheduler


def configure_agent_logging(data_dir: Path) -> logging.Logger:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("file-backup-client-agent")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            log_dir / "client-agent.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
    return logger


def retry_outbox(config: AppConfig, db: LocalDb, logger: logging.Logger) -> None:
    for job in db.list_incomplete_backup_jobs():
        backup_id = str(job["backup_id"])
        try:
            result = retry_backup_destinations(
                config,
                backup_id,
                local_db=db,
            )
            logger.info("outbox retry backup_id=%s status=%s", backup_id, result.status)
        except Exception as exc:
            logger.error("outbox retry failed backup_id=%s error=%s", backup_id, exc)


def run_agent(config_path: Path) -> None:
    config = load_config(config_path)
    logger = configure_agent_logging(config.client.data_dir)
    db = LocalDb(config.client.data_dir / "client.sqlite")
    retry_outbox(config, db, logger)
    scheduler = BackupTaskScheduler(config, logger.info)
    scheduler.start()
    logger.info("client agent started machine_id=%s", config.client.machine_id)

    stop_event = threading.Event()

    def stop_handler(*_: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    stop_event.wait()
    scheduler.stop()
    logger.info("client agent stopped")
