from __future__ import annotations

from fastapi.testclient import TestClient

from server.app.config import Settings
from server.app.main import create_app


def test_admin_page_is_served(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'app.sqlite'}",
        storage_root=tmp_path / "storage",
        manifest_root=tmp_path / "manifests",
        server_admin_token="admin-token",
        client_tokens={"trade-pc-01": "token-01"},
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/admin")

    assert response.status_code == 200
    assert "File Backup Server" in response.text
    assert "/api/v1/backups" in response.text
    assert "downloadBundle" in response.text


def test_root_redirects_to_admin(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'app.sqlite'}",
        storage_root=tmp_path / "storage",
        manifest_root=tmp_path / "manifests",
        server_admin_token="admin-token",
        client_tokens={"trade-pc-01": "token-01"},
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/", follow_redirects=False)

    assert response.status_code in {307, 308}
    assert response.headers["location"] == "/admin"
