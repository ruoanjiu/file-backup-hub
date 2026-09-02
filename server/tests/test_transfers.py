from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from server.tests.test_server_milestone1 import auth, make_client
from server.app.services.transfer_service import SERVER_RECEIVER_ID


def make_transfer_bundle(manifest: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_bytes)
        tar.addfile(manifest_info, io.BytesIO(manifest_bytes))
        content = b"hello transfer"
        file_info = tarfile.TarInfo(manifest["files"][0]["backup_path"])
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

        stored_bundle = tmp_path / "transfers" / "office-pc-02" / transfer_id / "bundle.tar.gz"
        assert stored_bundle.read_bytes() == bundle

        unauthorized = client.get(
            f"/api/v1/transfers/{transfer_id}",
            headers=auth("admin-token"),
        )
        assert unauthorized.status_code == 200


def test_transfer_can_target_server_inbox_and_be_received_or_rejected(
    tmp_path: Path,
) -> None:
    def send_to_server(client, transfer_id: str, content: bytes) -> Path:
        manifest = {
            "schema_version": "1.0",
            "transfer_id": transfer_id,
            "sender_device_id": "office-pc-01",
            "receiver_device_id": SERVER_RECEIVER_ID,
            "created_at": datetime(2026, 9, 1, 17, 0, tzinfo=UTC).isoformat(),
            "file_count": 1,
            "total_size": len(content),
            "files": [
                {
                    "file_id": "000001",
                    "relative_path": "server-report.txt",
                    "backup_path": "files/server-report.txt",
                    "file_name": "server-report.txt",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            ],
        }
        bundle = make_transfer_bundle(manifest)
        initialized = client.post(
            "/api/v1/transfers/init",
            json={
                "transfer_id": transfer_id,
                "sender_device_id": "office-pc-01",
                "receiver_device_id": SERVER_RECEIVER_ID,
                "file_count": 1,
                "total_size": len(content),
                "bundle_size": len(bundle),
                "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
                "expires_in_hours": 24,
                "manifest": manifest,
            },
            headers=auth("token-01"),
        )
        assert initialized.status_code == 201
        uploaded = client.put(
            f"/api/v1/transfers/{transfer_id}/bundle",
            content=bundle,
            headers=auth("token-01"),
        )
        assert uploaded.status_code == 200
        return tmp_path / "transfers" / SERVER_RECEIVER_ID / transfer_id / "bundle.tar.gz"

    with make_client(tmp_path) as client:
        receive_id = "transfer_to_server_receive"
        stored_bundle = send_to_server(client, receive_id, b"hello transfer")

        server_inbox = client.get(
            "/api/v1/transfers/server-inbox",
            headers=auth("admin-token"),
        )
        assert server_inbox.status_code == 200
        assert [item["transfer_id"] for item in server_inbox.json()["items"]] == [receive_id]
        assert client.get(
            "/api/v1/transfers/server-inbox",
            headers=auth("token-02"),
        ).status_code == 403

        received = client.post(
            f"/api/v1/transfers/{receive_id}/server-receive",
            headers=auth("admin-token"),
        )
        assert received.status_code == 200
        assert received.json()["status"] == "COMPLETED"
        destination = Path(received.json()["destination_path"])
        assert (destination / "server-report.txt").read_bytes() == b"hello transfer"
        assert stored_bundle.is_file()

        reject_id = "transfer_to_server_reject"
        rejected_bundle = send_to_server(client, reject_id, b"hello transfer")
        rejected = client.post(
            f"/api/v1/transfers/{reject_id}/reject",
            headers=auth("admin-token"),
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "REJECTED"
        assert rejected_bundle.is_file()
        inbox_after = client.get(
            "/api/v1/transfers/server-inbox",
            headers=auth("admin-token"),
        ).json()
        assert inbox_after["items"] == []
