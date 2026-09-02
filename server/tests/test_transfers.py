from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from server.tests.test_server_milestone1 import auth, make_client


def make_transfer_bundle(manifest: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_bytes)
        tar.addfile(manifest_info, io.BytesIO(manifest_bytes))
        content = b"hello transfer"
        file_info = tarfile.TarInfo("files/report.txt")
        file_info.size = len(content)
        tar.addfile(file_info, io.BytesIO(content))
    return buffer.getvalue()


def test_transfer_relay_accept_download_and_complete(tmp_path: Path) -> None:
    transfer_id = "transfer_20260829_a1b2c3d4"
    content = b"hello transfer"
    manifest = {
        "schema_version": "1.0",
        "transfer_id": transfer_id,
        "sender_device_id": "office-pc-01",
        "receiver_device_id": "office-pc-02",
        "created_at": datetime(2026, 8, 29, 12, 0, tzinfo=UTC).isoformat(),
        "file_count": 1,
        "total_size": len(content),
        "files": [
            {
                "file_id": "000001",
                "relative_path": "report.txt",
                "backup_path": "files/report.txt",
                "file_name": "report.txt",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        ],
    }
    bundle = make_transfer_bundle(manifest)
    payload = {
        "transfer_id": transfer_id,
        "sender_device_id": "office-pc-01",
        "receiver_device_id": "office-pc-02",
        "file_count": 1,
        "total_size": len(content),
        "bundle_size": len(bundle),
        "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
        "expires_in_hours": 24,
        "manifest": manifest,
    }

    with make_client(tmp_path) as client:
        initialized = client.post(
            "/api/v1/transfers/init",
            json=payload,
            headers=auth("token-01"),
        )
        assert initialized.status_code == 201
        assert initialized.json()["status"] == "PENDING"

        uploaded = client.put(
            f"/api/v1/transfers/{transfer_id}/bundle",
            content=bundle,
            headers=auth("token-01"),
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["status"] == "AVAILABLE"

        inbox = client.get("/api/v1/transfers/inbox", headers=auth("token-02"))
        assert inbox.status_code == 200
        assert inbox.json()["items"][0]["transfer_id"] == transfer_id

        before_accept = client.get(
            f"/api/v1/transfers/{transfer_id}/bundle",
            headers=auth("token-02"),
        )
        assert before_accept.status_code == 409

        accepted = client.post(
            f"/api/v1/transfers/{transfer_id}/accept",
            headers=auth("token-02"),
        )
        assert accepted.status_code == 200
        assert accepted.json()["status"] == "ACCEPTED"

        downloaded = client.get(
            f"/api/v1/transfers/{transfer_id}/bundle",
            headers=auth("token-02"),
        )
        assert downloaded.status_code == 200
        assert downloaded.content == bundle

        completed = client.post(
            f"/api/v1/transfers/{transfer_id}/complete",
            headers=auth("token-02"),
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "COMPLETED"

        inbox_after_complete = client.get(
            "/api/v1/transfers/inbox",
            headers=auth("token-02"),
        )
        assert inbox_after_complete.json()["total"] == 0

        stored_bundle = tmp_path / "transfers" / "office-pc-02" / transfer_id / "bundle.tar.gz"
        assert stored_bundle.read_bytes() == bundle

        unauthorized = client.get(
            f"/api/v1/transfers/{transfer_id}",
            headers=auth("admin-token"),
        )
        assert unauthorized.status_code == 200


def test_send_receive_and_reject_files_on_server_without_deleting_bundle(
    tmp_path: Path,
) -> None:
    content = b"server inbox content"

    def create_transfer(transfer_id: str) -> tuple[dict, bytes]:
        manifest = {
            "schema_version": "1.0",
            "transfer_id": transfer_id,
            "sender_device_id": "office-pc-01",
            "receiver_device_id": "__server__",
            "created_at": datetime(2026, 9, 1, 10, 0, tzinfo=UTC).isoformat(),
            "file_count": 1,
            "total_size": len(content),
            "files": [
                {
                    "file_id": "000001",
                    "relative_path": "server-report.txt",
                    "backup_path": "files/report.txt",
                    "file_name": "server-report.txt",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ],
        }
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest_bytes)
            archive.addfile(info, io.BytesIO(manifest_bytes))
            file_info = tarfile.TarInfo("files/report.txt")
            file_info.size = len(content)
            archive.addfile(file_info, io.BytesIO(content))
        bundle = buffer.getvalue()
        payload = {
            "transfer_id": transfer_id,
            "sender_device_id": "office-pc-01",
            "receiver_device_id": "__server__",
            "file_count": 1,
            "total_size": len(content),
            "bundle_size": len(bundle),
            "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
            "expires_in_hours": 24,
            "manifest": manifest,
        }
        return payload, bundle

    with make_client(tmp_path) as client:
        receive_id = "transfer_server_receive_01"
        receive_payload, receive_bundle = create_transfer(receive_id)
        assert client.post(
            "/api/v1/transfers/init",
            json=receive_payload,
            headers=auth("token-01"),
        ).status_code == 201
        assert client.put(
            f"/api/v1/transfers/{receive_id}/bundle",
            content=receive_bundle,
            headers=auth("token-01"),
        ).status_code == 200
        server_inbox = client.get(
            "/api/v1/transfers/inbox",
            headers=auth("admin-token"),
        )
        assert [item["transfer_id"] for item in server_inbox.json()["items"]] == [receive_id]
        received = client.post(
            f"/api/v1/transfers/{receive_id}/receive-on-server",
            headers=auth("admin-token"),
        )
        assert received.status_code == 200
        assert received.json()["status"] == "COMPLETED"
        assert received.json()["source_files_deleted"] is False
        received_file = (
            tmp_path
            / "transfers"
            / "server-inbox"
            / receive_id
            / "server-report.txt"
        )
        assert received_file.read_bytes() == content
        receive_stored_bundle = (
            tmp_path / "transfers" / "__server__" / receive_id / "bundle.tar.gz"
        )
        assert receive_stored_bundle.read_bytes() == receive_bundle
        assert client.get(
            "/api/v1/transfers/inbox",
            headers=auth("admin-token"),
        ).json()["total"] == 0

        reject_id = "transfer_server_reject_01"
        reject_payload, reject_bundle = create_transfer(reject_id)
        client.post(
            "/api/v1/transfers/init",
            json=reject_payload,
            headers=auth("token-01"),
        )
        client.put(
            f"/api/v1/transfers/{reject_id}/bundle",
            content=reject_bundle,
            headers=auth("token-01"),
        )
        rejected = client.post(
            f"/api/v1/transfers/{reject_id}/reject",
            headers=auth("admin-token"),
        )
        assert rejected.json()["status"] == "REJECTED"
        assert client.get(
            "/api/v1/transfers/inbox",
            headers=auth("admin-token"),
        ).json()["total"] == 0
        reject_stored_bundle = (
            tmp_path / "transfers" / "__server__" / reject_id / "bundle.tar.gz"
        )
        assert reject_stored_bundle.read_bytes() == reject_bundle
