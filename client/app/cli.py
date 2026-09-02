from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer
import yaml

from client.app.backup import retry_backup_destinations, run_backup_for_task
from client.app.config import default_config_path, load_config
from client.app.local_db import LocalDb
from client.app.restore import rollback_restore, run_restore, run_verify
from client.app.scheduler import BackupTaskScheduler
from client.app.transfer import receive_transfer, send_transfer
from client.app.uploader import BackupServerClient, list_backups_across_servers

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
        "servers": [
            {
                "id": server.id,
                "name": server.name,
                "base_url": server.base_url,
                "token": "***",
                "timeout_seconds": server.timeout_seconds,
                "verify_tls": server.verify_tls,
                "enabled": server.enabled,
            }
            for server in loaded.servers
        ],
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
    failed = 0
    for task in tasks:
        result = run_backup_for_task(loaded, task, local_db=local_db)
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
                    "destinations": [item.__dict__ for item in result.destinations],
                    "outbox_path": str(result.outbox_path) if result.outbox_path else None,
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
    server_id: Annotated[
        str,
        typer.Option("--server", help="Server ID, or 'all' to merge all copies"),
    ] = "all",
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    response = list_backups_across_servers(
        loaded,
        server_id=server_id,
        machine_id=machine_id or loaded.client.machine_id,
        task_name=task_name,
        limit=limit,
        offset=offset,
    )
    typer.echo(json.dumps(response, ensure_ascii=False, indent=2))


@app.command("retry")
def retry_command(
    backup_id: Annotated[str, typer.Option("--backup-id", help="Backup ID in local outbox")],
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    result = retry_backup_destinations(loaded, backup_id)
    typer.echo(
        json.dumps(
            {
                "backup_id": result.backup_id,
                "status": result.status,
                "destinations": [item.__dict__ for item in result.destinations],
                "outbox_path": str(result.outbox_path) if result.outbox_path else None,
                "error_message": result.error_message,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if result.status != "SUCCESS":
        raise typer.Exit(code=1)


@app.command("verify")
def verify_command(
    backup_id: Annotated[str, typer.Option("--backup-id", help="Backup ID to verify")],
    server_id: Annotated[
        str,
        typer.Option("--server", help="Server ID, or 'auto' for verified fallback"),
    ] = "auto",
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    result = run_verify(loaded, backup_id, server_id=server_id)
    typer.echo(
        json.dumps(
            {
                "backup_id": result.backup_id,
                "status": result.status,
                "file_count": result.file_count,
                "bundle_sha256": result.bundle_sha256,
                "server_id": result.server_id,
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
        typer.Option("--path-map", help="Path mapping, for example D:/BackupData=E:/BackupData"),
    ] = None,
    include: Annotated[
        list[str] | None,
        typer.Option("--include", help="Restore only files matching this glob"),
    ] = None,
    allow_cross_machine: Annotated[
        bool,
        typer.Option("--allow-cross-machine", help="Allow restoring a backup from another machine_id"),
    ] = False,
    server_id: Annotated[
        str,
        typer.Option("--server", help="Server ID, or 'auto' for verified fallback"),
    ] = "auto",
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    result = run_restore(
        loaded,
        backup_id,
        path_maps=path_map,
        includes=include,
        allow_cross_machine=allow_cross_machine,
        server_id=server_id,
    )
    typer.echo(
        json.dumps(
            {
                "restore_id": result.restore_id,
                "backup_id": result.backup_id,
                "status": result.status,
                "restored_count": result.restored_count,
                "rollback_dir": str(result.rollback_dir),
                "server_id": result.server_id,
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


def _persist_paired_token(
    config_path: Path,
    server_id: str,
    token: str,
    display_name: str,
) -> None:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    raw.setdefault("client", {})["display_name"] = display_name
    servers = raw.get("servers")
    if isinstance(servers, list):
        for server in servers:
            if isinstance(server, dict) and server.get("id") == server_id:
                server["token"] = token
                break
        else:
            raise ValueError(f"Server not found in config: {server_id}")
    elif isinstance(raw.get("server"), dict) and server_id == "server-1":
        raw["server"]["token"] = token
    else:
        raise ValueError(f"Server not found in config: {server_id}")
    config_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


@app.command("pair")
def pair_command(
    code: Annotated[str, typer.Option("--code", help="Six-digit one-time pairing code")],
    server_id: Annotated[str, typer.Option("--server", help="Server ID to pair with")],
    display_name: Annotated[str, typer.Option("--name", help="Device display name")],
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    server = loaded.get_server(server_id)
    response = BackupServerClient(server).pair_device(
        code,
        loaded.client.machine_id,
        display_name,
    )
    _persist_paired_token(config, server_id, str(response["token"]), display_name)
    safe = {key: value for key, value in response.items() if key != "token"}
    safe["token_saved"] = True
    typer.echo(json.dumps(safe, ensure_ascii=False, indent=2))


@app.command("devices")
def devices_command(
    server_id: Annotated[str, typer.Option("--server", help="Server ID")] = "server-1",
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    response = BackupServerClient(loaded.get_server(server_id)).list_devices()
    typer.echo(json.dumps(response, ensure_ascii=False, indent=2))


@app.command("rename-device")
def rename_device_command(
    display_name: Annotated[str, typer.Option("--name", help="New display name")],
    server_id: Annotated[str, typer.Option("--server", help="Server ID")] = "server-1",
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    response = BackupServerClient(loaded.get_server(server_id)).rename_device(
        loaded.client.machine_id,
        display_name,
    )
    _persist_paired_token(
        config,
        server_id,
        loaded.get_server(server_id).token,
        display_name,
    )
    typer.echo(json.dumps(response, ensure_ascii=False, indent=2))


@app.command("send")
def send_command(
    paths: Annotated[list[Path], typer.Argument(help="Files or folders to send")],
    receiver: Annotated[str, typer.Option("--to", help="Receiver device ID")],
    server_id: Annotated[
        str,
        typer.Option("--server", help="Server ID, or auto"),
    ] = "auto",
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    result = send_transfer(
        loaded,
        paths,
        receiver,
        server_id=server_id,
    )
    typer.echo(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str))
    if result.status != "AVAILABLE":
        raise typer.Exit(code=1)


@app.command("inbox")
def inbox_command(
    server_id: Annotated[str, typer.Option("--server", help="Server ID")] = "server-1",
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    response = BackupServerClient(loaded.get_server(server_id)).list_transfer_inbox()
    typer.echo(json.dumps(response, ensure_ascii=False, indent=2))


@app.command("receive")
def receive_command(
    transfer_id: Annotated[str, typer.Option("--transfer-id", help="Transfer ID")],
    server_id: Annotated[str, typer.Option("--server", help="Server ID")] = "server-1",
    destination: Annotated[
        Path | None,
        typer.Option("--destination", help="Local receive directory; defaults to Inbox"),
    ] = None,
    config: ConfigOption = default_config_path(),
) -> None:
    loaded = load_config(config)
    result = receive_transfer(
        loaded,
        transfer_id,
        server_id=server_id,
        destination=destination,
    )
    typer.echo(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str))
    if result.status != "COMPLETED":
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
