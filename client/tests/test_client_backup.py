from __future__ import annotations

import json
import tarfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from client.app.backup import prepare_backup, run_backup_for_task
from client.app.config import ServerSection, load_config
from client.app.local_db import LocalDb
from client.app.scanner import scan_task_files
from client.app.uploader import BackupServerClient
from client.app.utils.hashing import calculate_sha256


def write_config(tmp_path: Path, task_root: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
client:
  machine_id: "office-pc-01"
  timezone: "Asia/Shanghai"
  data_dir: "{(tmp_path / 'data').as_posix()}"
  temp_dir: "{(tmp_path / 'tmp').as_posix()}"

server:
  base_url: "https://backup.example.test"
  token: "token-01"
  timeout_seconds: 10
  verify_tls: true

backup:
  copy_stability_check: false
  stability_check_interval_seconds: 0

restore:
  allowed_roots:
    - "{task_root.as_posix()}"

tasks:
  - name: "daily_documents"
    enabled: true
    roots:
      - path: "{task_root.as_posix()}"
        recursive: true
        include:
          - "*.log"
          - "*.json"
          - "*.xlsx"
        exclude:
          - "*.tmp"
          - "~$*.xlsx"
          - "__pycache__/*"
""",
        encoding="utf-8",
    )
    return config_path


def write_dual_server_config(tmp_path: Path, task_root: Path) -> Path:
    config_path = write_config(tmp_path, task_root)
    raw = config_path.read_text(encoding="utf-8")
    raw = raw.replace(
        '''server:
  base_url: "https://backup.example.test"
  token: "token-01"
  timeout_seconds: 10
  verify_tls: true
''',
        '''servers:
  - id: "server-a"
    name: "Server A"
    base_url: "https://backup-a.example.test"
    token: "token-a"
    timeout_seconds: 10
    verify_tls: true
  - id: "server-b"
    name: "Server B"
    base_url: "https://backup-b.example.test"
    token: "token-b"
    timeout_seconds: 10
    verify_tls: true
''',
    )
    raw = raw.replace(
        "backup:\n",
        "backup:\n  required_copies: 2\n  retry_count: 0\n  retry_interval_seconds: 0\n",
    )
    config_path.write_text(raw, encoding="utf-8")
    return config_path


def make_task_files(root: Path) -> None:
    (root / "logs").mkdir(parents=True)
    (root / "nav").mkdir(parents=True)
    (root / "__pycache__").mkdir(parents=True)
    (root / "logs" / "a.log").write_text("hello log", encoding="utf-8")
    (root / "nav" / "state.json").write_text('{"ok": true}', encoding="utf-8")
    (root / "nav" / "nav.xlsx").write_bytes(b"xlsx-data")
    (root / "logs" / "skip.tmp").write_text("skip", encoding="utf-8")
    (root / "nav" / "~$nav.xlsx").write_bytes(b"excel-lock")
    (root / "__pycache__" / "cached.log").write_text("skip", encoding="utf-8")


def test_load_config_and_scan_files(tmp_path: Path) -> None:
    task_root = tmp_path / "task"
    make_task_files(task_root)
    config = load_config(write_config(tmp_path, task_root))

    assert config.client.machine_id == "office-pc-01"
    assert config.server.token == "token-01"

    files = scan_task_files(config.get_task("daily_documents"), config.backup)
    names = sorted(path.path.name for path in files)
    assert names == ["a.log", "nav.xlsx", "state.json"]


def test_config_supports_single_file_source_and_schedule(tmp_path: Path) -> None:
    task_root = tmp_path / "task"
    make_task_files(task_root)
    single_file = task_root / "logs" / "a.log"
    config_path = write_config(tmp_path, task_root)
    raw = config_path.read_text(encoding="utf-8")
    raw = raw.replace("    enabled: true\n", "    enabled: true\n    schedule_enabled: true\n    schedule_time: \"05:30\"\n", 1)
    raw = raw.replace(f'      - path: "{task_root.as_posix()}"', f'      - path: "{single_file.as_posix()}"')
    config_path.write_text(raw, encoding="utf-8")

    config = load_config(config_path)
    task = config.get_task("daily_documents")
    assert task.schedule_enabled is True
    assert task.schedule_time == "05:30"

    files = scan_task_files(task, config.backup)
    assert [item.path.name for item in files] == ["a.log"]


def test_prepare_backup_creates_manifest_and_bundle(tmp_path: Path) -> None:
    task_root = tmp_path / "task"
    make_task_files(task_root)
    config = load_config(write_config(tmp_path, task_root))
    created_at = datetime(2026, 4, 30, 4, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    prepared = prepare_backup(config, config.get_task("daily_documents"), created_at)

    assert prepared.file_count == 3
    assert prepared.total_size > 0
    assert prepared.bundle_path.exists()
    assert len(prepared.bundle_sha256) == 64
    assert prepared.manifest["backup_id"].startswith("office-pc-01__daily_documents__20260430_040000__")

    with tarfile.open(prepared.bundle_path, "r:gz") as tar:
        names = sorted(tar.getnames())
        assert names == sorted([
            "files/000001.log",
            "files/000002.xlsx",
            "files/000003.json",
            "manifest.json",
        ])
        manifest_file = tar.extractfile("manifest.json")
        assert manifest_file is not None
        manifest = json.loads(manifest_file.read().decode("utf-8"))
        assert manifest["file_count"] == 3
        assert all("original_path" in item for item in manifest["files"])


def test_uploader_sends_init_then_bundle(tmp_path: Path) -> None:
    bundle_path = tmp_path / "bundle.tar.gz"
    bundle_path.write_bytes(b"bundle")
    manifest = {
        "backup_id": "office-pc-01__daily_documents__20260430_040000__a1b2c3d4",
        "machine_id": "office-pc-01",
        "task_name": "daily_documents",
        "created_at": "2026-04-30T04:00:00+08:00",
        "file_count": 0,
        "total_size": 0,
        "files": [],
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        assert request.headers["authorization"] == "Bearer token-01"
        if request.url.path.endswith("/init"):
            return httpx.Response(
                201,
                json={
                    "backup_id": manifest["backup_id"],
                    "status": "PENDING",
                    "upload_url": f"/api/v1/backups/{manifest['backup_id']}/bundle",
                },
            )
        return httpx.Response(
            200,
            json={
                "backup_id": manifest["backup_id"],
                "status": "COMPLETED",
                "bundle_sha256": "abc",
            },
        )

    client = BackupServerClient(
        ServerSection("https://backup.example.test", "token-01"),
        transport=httpx.MockTransport(handler),
    )
    response = client.upload_backup(manifest, bundle_path, "abc")

    assert response["status"] == "COMPLETED"
    assert seen == [
        "POST /api/v1/backups/init",
        f"PUT /api/v1/backups/{manifest['backup_id']}/bundle",
    ]


def test_run_backup_records_local_db(tmp_path: Path) -> None:
    task_root = tmp_path / "task"
    make_task_files(task_root)
    config = load_config(write_config(tmp_path, task_root))
    local_db = LocalDb(tmp_path / "client.sqlite")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/init"):
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                201,
                json={
                    "backup_id": body["backup_id"],
                    "status": "PENDING",
                    "upload_url": f"/api/v1/backups/{body['backup_id']}/bundle",
                },
            )
        return httpx.Response(200, json={"status": "COMPLETED"})

    server_client = BackupServerClient(
        ServerSection("https://backup.example.test", "token-01"),
        transport=httpx.MockTransport(handler),
    )
    result = run_backup_for_task(
        config,
        config.get_task("daily_documents"),
        server_client=server_client,
        local_db=local_db,
    )

    assert result.status == "SUCCESS"
    assert result.file_count == 3


def test_dual_server_backup_keeps_sources_and_retries_missing_copy(tmp_path: Path) -> None:
    task_root = tmp_path / "task"
    make_task_files(task_root)
    config = load_config(write_dual_server_config(tmp_path, task_root))
    local_db = LocalDb(tmp_path / "client.sqlite")
    source_hashes = {
        path: calculate_sha256(path)
        for path in task_root.rglob("*")
        if path.is_file()
    }

    def make_transport(server_id: str, fail_upload: bool = False) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/init"):
                body = json.loads(request.content.decode("utf-8"))
                return httpx.Response(
                    201,
                    json={
                        "backup_id": body["backup_id"],
                        "status": "PENDING",
                        "upload_url": f"/api/v1/backups/{body['backup_id']}/bundle",
                    },
                )
            if fail_upload:
                return httpx.Response(503, json={"detail": f"{server_id} unavailable"})
            return httpx.Response(200, json={"status": "COMPLETED"})

        return httpx.MockTransport(handler)

    clients = {
        "server-a": BackupServerClient(
            config.get_server("server-a"),
            transport=make_transport("server-a"),
        ),
        "server-b": BackupServerClient(
            config.get_server("server-b"),
            transport=make_transport("server-b", fail_upload=True),
        ),
    }
    result = run_backup_for_task(
        config,
        config.get_task("daily_documents"),
        server_clients=clients,
        local_db=local_db,
    )

    assert result.status == "DEGRADED"
    assert result.outbox_path is not None and result.outbox_path.exists()
    assert {item.server_id: item.status for item in result.destinations} == {
        "server-a": "COMPLETED",
        "server-b": "FAILED",
    }
    assert all(path.exists() for path in source_hashes)
    assert {path: calculate_sha256(path) for path in source_hashes} == source_hashes

    from client.app.backup import retry_backup_destinations

    retry_result = retry_backup_destinations(
        config,
        result.backup_id,
        local_db=local_db,
        server_clients={
            "server-b": BackupServerClient(
                config.get_server("server-b"),
                transport=make_transport("server-b"),
            )
        },
    )
    assert retry_result.status == "SUCCESS"
    assert not (config.client.outbox_dir / result.backup_id).exists()
    assert all(path.exists() for path in source_hashes)
    assert {path: calculate_sha256(path) for path in source_hashes} == source_hashes
