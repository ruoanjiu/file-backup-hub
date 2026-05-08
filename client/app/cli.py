from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer

from client.app.backup import run_backup_for_task
from client.app.config import default_config_path, load_config
from client.app.local_db import LocalDb
from client.app.restore import rollback_restore, run_restore, run_verify
from client.app.scheduler import BackupTaskScheduler
from client.app.uploader import BackupServerClient

app = typer.Typer(help="File backup client")
config_app = typer.Typer(help="Configuration commands")
app.add_typer(config_app, name="config")


ConfigOption = Annotated[
    Path,
    typer.Option("--config", "-c", help="Path to client config.yaml"),
]


@config_app.command("show")
def show_config(config: ConfigOption = default_config_path()) -> None:
    loaded = load_config(config)
    safe = {
        "client": {
            "machine_id": loaded.client.machine_id,
            "timezone": loaded.client.timezone,
            "data_dir": str(loaded.client.data_dir),
            "temp_dir": str(loaded.client.temp_dir),
        },
        "server": {
            "base_url": loaded.server.base_url,
            "token": "***",
            "timeout_seconds": loaded.server.timeout_seconds,
            "verify_tls": loaded.server.verify_tls,
        },
        "tasks": [
            {"name": task.name, "enabled": task.enabled}
            for task in loaded.tasks
        ],
    }
    typer.echo(json.dumps(safe, ensure_ascii=False, indent=2))


@app.command("backup")
def backup_command(
    all_tasks: Annotated[bool, typer.Option("--all", help="Back up all enabled tasks")] = False,
    task_name: Annotated[str | None, typer.Option("--task", help="Back up one task")] = None,
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    if all_tasks:
        tasks = loaded.enabled_tasks()
    elif task_name:
        task = loaded.get_task(task_name)
        tasks = [task] if task.enabled else []
    else:
        raise typer.BadParameter("Use --all or --task")

    if not tasks:
        raise typer.BadParameter("No enabled tasks selected")

    local_db = LocalDb(loaded.client.data_dir / "client.sqlite")
    server_client = BackupServerClient(loaded.server)
    failed = 0
    for task in tasks:
        result = run_backup_for_task(loaded, task, server_client, local_db)
        typer.echo(
            json.dumps(
                {
                    "backup_id": result.backup_id,
                    "task_name": result.task_name,
                    "status": result.status,
                    "file_count": result.file_count,
                    "total_size": result.total_size,
                    "bundle_sha256": result.bundle_sha256,
                    "error_message": result.error_message,
                },
                ensure_ascii=False,
            )
        )
        if result.status != "SUCCESS":
            failed += 1
    if failed:
        raise typer.Exit(code=1)


@app.command("list")
def list_command(
    task_name: Annotated[str | None, typer.Option("--task", help="Filter by task")] = None,
    machine_id: Annotated[str | None, typer.Option("--machine", help="Filter by machine_id")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500)] = 50,
    offset: Annotated[int, typer.Option("--offset", min=0)] = 0,
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    response = BackupServerClient(loaded.server).list_backups(
        machine_id=machine_id or loaded.client.machine_id,
        task_name=task_name,
        limit=limit,
        offset=offset,
    )
    typer.echo(json.dumps(response, ensure_ascii=False, indent=2))


@app.command("delete")
def delete_command(
    backup_id: Annotated[str, typer.Option("--backup-id", help="Backup ID to delete")],
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    response = BackupServerClient(loaded.server).delete_backup(backup_id)
    typer.echo(json.dumps(response, ensure_ascii=False, indent=2))


@app.command("verify")
def verify_command(
    backup_id: Annotated[str, typer.Option("--backup-id", help="Backup ID to verify")],
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    result = run_verify(loaded, backup_id)
    typer.echo(
        json.dumps(
            {
                "backup_id": result.backup_id,
                "status": result.status,
                "file_count": result.file_count,
                "bundle_sha256": result.bundle_sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("restore")
def restore_command(
    backup_id: Annotated[str, typer.Option("--backup-id", help="Backup ID to restore")],
    path_map: Annotated[
        list[str] | None,
        typer.Option("--path-map", help="Path mapping, for example D:/trade=E:/trade"),
    ] = None,
    include: Annotated[
        list[str] | None,
        typer.Option("--include", help="Restore only files matching this glob"),
    ] = None,
    allow_cross_machine: Annotated[
        bool,
        typer.Option("--allow-cross-machine", help="Allow restoring a backup from another machine_id"),
    ] = False,
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    result = run_restore(
        loaded,
        backup_id,
        path_maps=path_map,
        includes=include,
        allow_cross_machine=allow_cross_machine,
    )
    typer.echo(
        json.dumps(
            {
                "restore_id": result.restore_id,
                "backup_id": result.backup_id,
                "status": result.status,
                "restored_count": result.restored_count,
                "rollback_dir": str(result.rollback_dir),
                "error_message": result.error_message,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if result.status != "SUCCESS":
        raise typer.Exit(code=1)


@app.command("rollback")
def rollback_command(
    restore_id: Annotated[str, typer.Option("--restore-id", help="Restore ID to roll back")],
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    result = rollback_restore(loaded, restore_id)
    typer.echo(
        json.dumps(
            {
                "restore_id": result.restore_id,
                "status": result.status,
                "rolled_back_count": result.rolled_back_count,
                "error_message": result.error_message,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if result.status != "SUCCESS":
        raise typer.Exit(code=1)


@app.command("scheduler")
def scheduler_command(config: ConfigOption = default_config_path()) -> None:
    loaded = load_config(config)
    scheduler = BackupTaskScheduler(loaded, typer.echo)
    scheduler.start()
    typer.echo("Scheduler running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.stop()


@app.command("gui")
def gui_command() -> None:
    from client.app.gui import main

    main()


if __name__ == "__main__":
    app()
