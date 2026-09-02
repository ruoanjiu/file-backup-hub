from __future__ import annotations

from pathlib import Path

from server.tests.test_server_milestone1 import auth, make_client


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

        forbidden_revoke = client.post(
            "/api/v1/devices/office-pc/revoke",
            headers=auth("token-02"),
        )
        assert forbidden_revoke.status_code == 403

        revoked = client.post(
            "/api/v1/devices/office-pc/revoke",
            headers=auth("admin-token"),
        )
        assert revoked.status_code == 200
        assert revoked.json() == {
            "device_id": "office-pc",
            "status": "REVOKED",
            "backups_deleted": False,
        }
        assert client.get("/api/v1/devices", headers=auth(token)).status_code == 401
        disabled = {
            item["device_id"]: item
            for item in client.get(
                "/api/v1/devices",
                headers=auth("admin-token"),
            ).json()["items"]
        }
        assert disabled["office-pc"]["enabled"] is False

        repair_code = client.post(
            "/api/v1/pairing/codes",
            json={"lifetime_minutes": 5},
            headers=auth("admin-token"),
        ).json()["code"]
        repaired = client.post(
            "/api/v1/devices/pair",
            json={
                "code": repair_code,
                "device_id": "office-pc",
                "display_name": "办公室电脑重新配对",
            },
        )
        assert repaired.status_code == 200
        new_token = repaired.json()["token"]
        assert new_token != token
        assert client.get("/api/v1/devices", headers=auth(token)).status_code == 401
        assert client.get("/api/v1/devices", headers=auth(new_token)).status_code == 200

        active_remove = client.delete(
            "/api/v1/devices/office-pc",
            headers=auth("admin-token"),
        )
        assert active_remove.status_code == 409

        self_revoked = client.post(
            "/api/v1/devices/self/revoke",
            headers=auth(new_token),
        )
        assert self_revoked.status_code == 200
        assert self_revoked.json()["backups_deleted"] is False
        assert client.get("/api/v1/devices", headers=auth(new_token)).status_code == 401

        removed = client.delete(
            "/api/v1/devices/office-pc",
            headers=auth("admin-token"),
        )
        assert removed.status_code == 200
        assert removed.json() == {
            "device_id": "office-pc",
            "status": "REMOVED",
            "device_record_only": True,
            "backups_deleted": False,
        }
        after_remove = client.get(
            "/api/v1/devices",
            headers=auth("admin-token"),
        ).json()["items"]
        assert all(item["device_id"] != "office-pc" for item in after_remove)
