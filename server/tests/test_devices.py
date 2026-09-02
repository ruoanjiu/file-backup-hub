from __future__ import annotations

import sqlite3
from pathlib import Path

from server.tests.test_server_milestone1 import auth, make_client


def pair_device(client, device_id: str, display_name: str) -> str:
    code_response = client.post(
        "/api/v1/pairing/codes",
        json={"lifetime_minutes": 5},
        headers=auth("admin-token"),
    )
    assert code_response.status_code == 200
    pair_response = client.post(
        "/api/v1/devices/pair",
        json={
            "code": code_response.json()["code"],
            "device_id": device_id,
            "display_name": display_name,
        },
    )
    assert pair_response.status_code == 200
    return str(pair_response.json()["token"])


def test_pair_list_and_rename_device(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        code_response = client.post(
            "/api/v1/pairing/codes",
            json={"lifetime_minutes": 5},
            headers=auth("admin-token"),
        )
        assert code_response.status_code == 200
        code = code_response.json()["code"]
        assert len(code) == 6 and code.isdigit()

        pair_response = client.post(
            "/api/v1/devices/pair",
            json={
                "code": code,
                "device_id": "office-pc",
                "display_name": "办公室电脑",
            },
        )
        assert pair_response.status_code == 200
        token = pair_response.json()["token"]
        assert token.startswith("fb1:office-pc:")

        reused = client.post(
            "/api/v1/devices/pair",
            json={
                "code": code,
                "device_id": "other-pc",
                "display_name": "另一台电脑",
            },
        )
        assert reused.status_code == 400

        device_list = client.get("/api/v1/devices", headers=auth(token))
        assert device_list.status_code == 200
        devices = {item["device_id"]: item for item in device_list.json()["items"]}
        assert devices["office-pc"]["display_name"] == "办公室电脑"
        assert devices["office-pc"]["paired"] is True
        assert devices["office-pc-01"]["paired"] is False

        renamed = client.patch(
            "/api/v1/devices/office-pc",
            json={"display_name": "办公室电脑 A"},
            headers=auth(token),
        )
        assert renamed.status_code == 200
        assert renamed.json()["display_name"] == "办公室电脑 A"

        forbidden = client.patch(
            "/api/v1/devices/office-pc",
            json={"display_name": "不允许的名称"},
            headers=auth("token-02"),
        )
        assert forbidden.status_code == 403


def test_admin_revoke_invalidates_token_and_repair_reuses_client_row(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.sqlite"
    with make_client(tmp_path) as client:
        old_token = pair_device(client, "office-pc", "办公室电脑")
        with sqlite3.connect(database_path) as db:
            client_row_id = db.execute(
                "SELECT id FROM clients WHERE machine_id = ?",
                ("office-pc",),
            ).fetchone()[0]
            db.execute(
                """
                INSERT INTO backups (
                    backup_id, machine_id, task_name, status, created_at,
                    file_count, total_size
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "office-pc__logs__history",
                    "office-pc",
                    "logs",
                    "COMPLETED",
                    "2026-09-01T00:00:00+00:00",
                    1,
                    12,
                ),
            )
            db.execute(
                """
                INSERT INTO transfers (
                    transfer_id, sender_device_id, receiver_device_id, status,
                    created_at, updated_at, expires_at, file_count, total_size,
                    bundle_size, bundle_sha256, storage_path, manifest_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "transfer_history",
                    "office-pc",
                    "office-pc-01",
                    "AVAILABLE",
                    "2026-09-01T00:00:00+00:00",
                    "2026-09-01T00:00:00+00:00",
                    "2026-09-02T00:00:00+00:00",
                    1,
                    12,
                    12,
                    "0" * 64,
                    str(tmp_path / "transfers" / "bundle.tar.gz"),
                    str(tmp_path / "transfers" / "manifest.json"),
                ),
            )
            db.commit()

        sentinels = [
            tmp_path / "storage" / "original.bin",
            tmp_path / "manifests" / "manifest.json",
            tmp_path / "transfers" / "transfer.bin",
            tmp_path / "trash" / "old-backup.bin",
        ]
        for sentinel in sentinels:
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_bytes(b"preserve-me")

        revoked = client.post(
            "/api/v1/devices/office-pc/revoke",
            headers=auth("admin-token"),
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "REVOKED"
        assert revoked.json()["enabled"] is False

        assert client.get("/api/v1/devices", headers=auth(old_token)).status_code == 401
        listed = client.get("/api/v1/devices", headers=auth("admin-token")).json()["items"]
        office = next(item for item in listed if item["device_id"] == "office-pc")
        assert office["enabled"] is False

        new_token = pair_device(client, "office-pc", "办公室电脑（重新配对）")
        assert new_token != old_token
        assert client.get("/api/v1/devices", headers=auth(old_token)).status_code == 401
        assert client.get("/api/v1/devices", headers=auth(new_token)).status_code == 200

        with sqlite3.connect(database_path) as db:
            rows = db.execute(
                "SELECT id, enabled FROM clients WHERE machine_id = ?",
                ("office-pc",),
            ).fetchall()
            assert rows == [(client_row_id, 1)]
            assert db.execute(
                "SELECT COUNT(*) FROM backups WHERE machine_id = ?",
                ("office-pc",),
            ).fetchone()[0] == 1
            assert db.execute(
                "SELECT COUNT(*) FROM transfers WHERE sender_device_id = ?",
                ("office-pc",),
            ).fetchone()[0] == 1
            actions = {
                row[0]
                for row in db.execute(
                    "SELECT action FROM audit_logs WHERE target = ?",
                    ("office-pc",),
                ).fetchall()
            }
            assert "device.revoked.admin" in actions
            assert "device.reactivated" in actions
        assert all(path.read_bytes() == b"preserve-me" for path in sentinels)


def test_device_can_revoke_itself_and_admin_endpoint_requires_admin(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        token = pair_device(client, "self-pc", "本机")

        forbidden = client.post(
            "/api/v1/devices/office-pc-01/revoke",
            headers=auth(token),
        )
        assert forbidden.status_code == 403

        revoked = client.post(
            "/api/v1/devices/self/revoke",
            headers=auth(token),
        )
        assert revoked.status_code == 200
        assert revoked.json() == {
            "device_id": "self-pc",
            "status": "REVOKED",
            "enabled": False,
        }
        assert client.get("/api/v1/devices", headers=auth(token)).status_code == 401

        admin_cannot_self_revoke = client.post(
            "/api/v1/devices/self/revoke",
            headers=auth("admin-token"),
        )
        assert admin_cannot_self_revoke.status_code == 403


def test_admin_can_remove_only_revoked_device_record_and_keep_backup_history(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.sqlite"
    with make_client(tmp_path) as client:
        pair_device(client, "removable-pc", "可移除设备")
        with sqlite3.connect(database_path) as db:
            db.execute(
                """
                INSERT INTO backups (
                    backup_id, machine_id, task_name, status, created_at,
                    file_count, total_size
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "removable-pc__documents__history",
                    "removable-pc",
                    "documents",
                    "COMPLETED",
                    "2026-09-01T00:00:00+00:00",
                    1,
                    12,
                ),
            )
            db.commit()

        active_remove = client.delete(
            "/api/v1/devices/removable-pc",
            headers=auth("admin-token"),
        )
        assert active_remove.status_code == 409
        client.post(
            "/api/v1/devices/removable-pc/revoke",
            headers=auth("admin-token"),
        )
        removed = client.delete(
            "/api/v1/devices/removable-pc",
            headers=auth("admin-token"),
        )
        assert removed.status_code == 200
        assert removed.json()["backups_deleted"] is False
        assert removed.json()["device_record_only"] is True
        with sqlite3.connect(database_path) as db:
            assert db.execute(
                "SELECT COUNT(*) FROM clients WHERE machine_id = ?",
                ("removable-pc",),
            ).fetchone()[0] == 0
            assert db.execute(
                "SELECT COUNT(*) FROM backups WHERE machine_id = ?",
                ("removable-pc",),
            ).fetchone()[0] == 1

        legacy_not_paired = client.post(
            "/api/v1/devices/office-pc-01/revoke",
            headers=auth("admin-token"),
        )
        assert legacy_not_paired.status_code == 404
