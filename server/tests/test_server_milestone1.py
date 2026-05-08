from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from server.app.config import Settings
from server.app.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'app.sqlite'}",
        storage_root=tmp_path / "storage",
        manifest_root=tmp_path / "manifests",
        server_admin_token="admin-token",
        client_tokens={
            "trade-pc-01": "token-01",
            "trade-pc-02": "token-02",
        },
    )
    return TestClient(create_app(settings))


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def sample_manifest(backup_id: str, machine_id: str = "trade-pc-01") -> dict:
    return {
        "schema_version": "1.0",
        "backup_id": backup_id,
        "machine_id": machine_id,
        "task_name": "alpha_grid",
        "created_at": "2026-04-30T04:00:00+08:00",
        "timezone": "Asia/Shanghai",
        "archive_format": "tar.gz",
        "file_count": 1,
        "total_size": 11,
        "roots": ["D:/trade/alpha_grid/logs"],
        "files": [
            {
                "file_id": "000001",
                "original_path": "D:/trade/alpha_grid/logs/a.log",
                "backup_path": "files/000001.log",
                "file_name": "a.log",
                "file_type": "log",
                "size": 11,
                "mtime": 1777500000.0,
                "sha256": hashlib.sha256(b"hello world").hexdigest(),
                "possibly_active": False,
            }
        ],
    }


def make_bundle(manifest: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_bytes)
        tar.addfile(manifest_info, io.BytesIO(manifest_bytes))

        file_bytes = b"hello world"
        file_info = tarfile.TarInfo("files/000001.log")
        file_info.size = len(file_bytes)
        tar.addfile(file_info, io.BytesIO(file_bytes))
    return buffer.getvalue()


def init_payload(backup_id: str, bundle: bytes, manifest: dict) -> dict:
    return {
        "backup_id": backup_id,
        "machine_id": manifest["machine_id"],
        "task_name": manifest["task_name"],
        "created_at": datetime(2026, 4, 30, 4, 0, tzinfo=UTC).isoformat(),
        "file_count": 1,
        "total_size": 11,
        "bundle_size": len(bundle),
        "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
        "manifest": manifest,
    }


def test_health_works_without_auth(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "app": "file-backup-server"}


def test_backup_init_upload_list_manifest_and_download(tmp_path: Path) -> None:
    backup_id = "trade-pc-01__alpha_grid__20260430_040000__a1b2c3d4"
    manifest = sample_manifest(backup_id)
    bundle = make_bundle(manifest)

    with make_client(tmp_path) as client:
        init_response = client.post(
            "/api/v1/backups/init",
            json=init_payload(backup_id, bundle, manifest),
            headers=auth("token-01"),
        )
        assert init_response.status_code == 201
        assert init_response.json()["status"] == "PENDING"

        upload_response = client.put(
            f"/api/v1/backups/{backup_id}/bundle",
            content=bundle,
            headers=auth("token-01"),
        )
        assert upload_response.status_code == 200
        assert upload_response.json()["status"] == "COMPLETED"

        list_response = client.get(
            "/api/v1/backups?machine_id=trade-pc-01&task_name=alpha_grid",
            headers=auth("token-01"),
        )
        assert list_response.status_code == 200
        list_body = list_response.json()
        assert list_body["total"] == 1
        assert list_body["items"][0]["backup_id"] == backup_id
        assert list_body["items"][0]["status"] == "COMPLETED"

        metadata_response = client.get(
            f"/api/v1/backups/{backup_id}",
            headers=auth("token-01"),
        )
        assert metadata_response.status_code == 200
        assert metadata_response.json()["bundle_sha256"] == hashlib.sha256(bundle).hexdigest()

        manifest_response = client.get(
            f"/api/v1/backups/{backup_id}/manifest",
            headers=auth("token-01"),
        )
        assert manifest_response.status_code == 200
        assert manifest_response.json()["backup_id"] == backup_id

        bundle_response = client.get(
            f"/api/v1/backups/{backup_id}/bundle",
            headers=auth("token-01"),
        )
        assert bundle_response.status_code == 200
        assert bundle_response.content == bundle

        delete_response = client.delete(
            f"/api/v1/backups/{backup_id}",
            headers=auth("token-01"),
        )
        assert delete_response.status_code == 200
        assert delete_response.json()["status"] == "DELETED"

        list_after_delete = client.get(
            "/api/v1/backups?machine_id=trade-pc-01&task_name=alpha_grid",
            headers=auth("token-01"),
        )
        assert list_after_delete.status_code == 200
        assert list_after_delete.json()["total"] == 0


def test_client_cannot_access_another_machine(tmp_path: Path) -> None:
    backup_id = "trade-pc-01__alpha_grid__20260430_040000__a1b2c3d4"
    manifest = sample_manifest(backup_id)
    bundle = make_bundle(manifest)

    with make_client(tmp_path) as client:
        response = client.post(
            "/api/v1/backups/init",
            json=init_payload(backup_id, bundle, manifest),
            headers=auth("token-02"),
        )
        assert response.status_code == 403

        list_response = client.get(
            "/api/v1/backups?machine_id=trade-pc-01",
            headers=auth("token-02"),
        )
        assert list_response.status_code == 403


def test_upload_rejects_sha256_mismatch(tmp_path: Path) -> None:
    backup_id = "trade-pc-01__alpha_grid__20260430_040000__a1b2c3d4"
    manifest = sample_manifest(backup_id)
    bundle = make_bundle(manifest)

    with make_client(tmp_path) as client:
        init_response = client.post(
            "/api/v1/backups/init",
            json=init_payload(backup_id, bundle, manifest),
            headers=auth("token-01"),
        )
        assert init_response.status_code == 201

        upload_response = client.put(
            f"/api/v1/backups/{backup_id}/bundle",
            content=b"tampered",
            headers=auth("token-01"),
        )
        assert upload_response.status_code == 400
        assert "SHA256" in upload_response.json()["detail"]
