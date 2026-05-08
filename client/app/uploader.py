from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from client.app.config import ServerSection


class BackupServerClient:
    def __init__(
        self,
        server: ServerSection,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.server = server
        self._transport = transport

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.server.token}"}

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.server.base_url,
            timeout=self.server.timeout_seconds,
            verify=self.server.verify_tls,
            transport=self._transport,
            trust_env=False,
        )

    def init_backup(self, manifest: dict, bundle_path: Path, bundle_sha256: str) -> dict[str, Any]:
        payload = {
            "backup_id": manifest["backup_id"],
            "machine_id": manifest["machine_id"],
            "strategy_name": manifest["strategy_name"],
            "created_at": manifest["created_at"],
            "file_count": manifest["file_count"],
            "total_size": manifest["total_size"],
            "bundle_size": bundle_path.stat().st_size,
            "bundle_sha256": bundle_sha256,
            "manifest": manifest,
        }
        with self._client() as client:
            response = client.post("/api/v1/backups/init", json=payload, headers=self._headers)
            response.raise_for_status()
            return response.json()

    def upload_bundle(self, backup_id: str, upload_url: str, bundle_path: Path) -> dict[str, Any]:
        with bundle_path.open("rb") as file_obj:
            with self._client() as client:
                response = client.put(
                    upload_url or f"/api/v1/backups/{backup_id}/bundle",
                    content=file_obj,
                    headers={**self._headers, "Content-Type": "application/gzip"},
                )
                response.raise_for_status()
                return response.json()

    def upload_backup(self, manifest: dict, bundle_path: Path, bundle_sha256: str) -> dict[str, Any]:
        init_response = self.init_backup(manifest, bundle_path, bundle_sha256)
        return self.upload_bundle(
            manifest["backup_id"],
            init_response.get("upload_url", ""),
            bundle_path,
        )

    def list_backups(
        self,
        machine_id: str | None = None,
        strategy_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if machine_id:
            params["machine_id"] = machine_id
        if strategy_name:
            params["strategy_name"] = strategy_name
        with self._client() as client:
            response = client.get("/api/v1/backups", params=params, headers=self._headers)
            response.raise_for_status()
            return response.json()

    def health(self) -> dict[str, Any]:
        with self._client() as client:
            response = client.get("/health")
            response.raise_for_status()
            return response.json()

    def get_backup_metadata(self, backup_id: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.get(f"/api/v1/backups/{backup_id}", headers=self._headers)
            response.raise_for_status()
            return response.json()

    def download_manifest(self, backup_id: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.get(f"/api/v1/backups/{backup_id}/manifest", headers=self._headers)
            response.raise_for_status()
            return response.json()

    def download_bundle(self, backup_id: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_name(destination.name + ".downloading")
        with self._client() as client:
            with client.stream(
                "GET",
                f"/api/v1/backups/{backup_id}/bundle",
                headers=self._headers,
            ) as response:
                response.raise_for_status()
                with temp_path.open("wb") as file_obj:
                    for chunk in response.iter_bytes():
                        if chunk:
                            file_obj.write(chunk)
        temp_path.replace(destination)
        return destination

    def delete_backup(self, backup_id: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.delete(f"/api/v1/backups/{backup_id}", headers=self._headers)
            response.raise_for_status()
            return response.json()
