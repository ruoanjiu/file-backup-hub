from __future__ import annotations

import json
from pathlib import Path

import httpx

from client.app.backup import prepare_backup
from client.app.config import ServerSection, load_config
from client.app.local_db import LocalDb
from client.app.restore import rollback_restore, run_restore, run_verify
from client.app.uploader import BackupServerClient
from client.tests.test_client_backup import make_strategy_files, write_config


def _mock_restore_client(manifest: dict, bundle: bytes, bundle_sha256: str) -> httpx.MockTransport:
    backup_id = manifest["backup_id"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/backups/{backup_id}":
            return httpx.Response(
                200,
                json={
                    "backup_id": backup_id,
                    "machine_id": manifest["machine_id"],
                    "strategy_name": manifest["strategy_name"],
                    "status": "COMPLETED",
                    "created_at": manifest["created_at"],
                    "uploaded_at": manifest["created_at"],
                    "file_count": manifest["file_count"],
                    "total_size": manifest["total_size"],
                    "bundle_size": len(bundle),
                    "bundle_sha256": bundle_sha256,
                },
            )
        if request.url.path == f"/api/v1/backups/{backup_id}/manifest":
            return httpx.Response(200, json=manifest)
        if request.url.path == f"/api/v1/backups/{backup_id}/bundle":
            return httpx.Response(200, content=bundle)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_verify_restore_and_rollback(tmp_path: Path) -> None:
    strategy_root = tmp_path / "strategy"
    make_strategy_files(strategy_root)
    config = load_config(write_config(tmp_path, strategy_root))
    prepared = prepare_backup(config, config.get_strategy("alpha_grid"))
    bundle = prepared.bundle_path.read_bytes()
    server_client = BackupServerClient(
        ServerSection("https://backup.example.test", "token-01"),
        transport=_mock_restore_client(prepared.manifest, bundle, prepared.bundle_sha256),
    )
    local_db = LocalDb(tmp_path / "client.sqlite")

    changed_file = strategy_root / "logs" / "a.log"
    deleted_file = strategy_root / "nav" / "state.json"
    changed_file.write_text("changed current", encoding="utf-8")
    deleted_file.unlink()

    verify_result = run_verify(config, prepared.backup_id, server_client=server_client)
    assert verify_result.status == "SUCCESS"
    assert verify_result.file_count == 3

    restore_result = run_restore(
        config,
        prepared.backup_id,
        server_client=server_client,
        local_db=local_db,
    )
    assert restore_result.status == "SUCCESS"
    assert restore_result.restored_count == 3
    assert changed_file.read_text(encoding="utf-8") == "hello log"
    assert json.loads(deleted_file.read_text(encoding="utf-8")) == {"ok": True}

    rollback_result = rollback_restore(config, restore_result.restore_id, local_db=local_db)
    assert rollback_result.status == "SUCCESS"
    assert rollback_result.rolled_back_count == 3
    assert changed_file.read_text(encoding="utf-8") == "changed current"
    assert not deleted_file.exists()
