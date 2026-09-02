from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from client.app.config import AppConfig, ServerSection


@dataclass(frozen=True)
class DestinationUploadResult:
    server_id: str
    server_name: str
    base_url: str
    status: str
    attempts: int
    bundle_sha256: str | None = None
    error_message: str | None = None


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
            "task_name": manifest["task_name"],
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
        if init_response.get("status") == "COMPLETED":
            return {
                "backup_id": manifest["backup_id"],
                "status": "COMPLETED",
                "bundle_sha256": bundle_sha256,
                "already_completed": True,
            }
        return self.upload_bundle(
            manifest["backup_id"],
            init_response.get("upload_url", ""),
            bundle_path,
        )

    def list_backups(
        self,
        machine_id: str | None = None,
        task_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if machine_id:
            params["machine_id"] = machine_id
        if task_name:
            params["task_name"] = task_name
        with self._client() as client:
            response = client.get("/api/v1/backups", params=params, headers=self._headers)
            response.raise_for_status()
            return response.json()

    def health(self) -> dict[str, Any]:
        with self._client() as client:
            response = client.get("/health")
            response.raise_for_status()
            return response.json()

    def create_pairing_code(self, lifetime_minutes: int = 5) -> dict[str, Any]:
        with self._client() as client:
            response = client.post(
                "/api/v1/pairing/codes",
                json={"lifetime_minutes": lifetime_minutes},
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    def pair_device(self, code: str, device_id: str, display_name: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.post(
                "/api/v1/devices/pair",
                json={
                    "code": code,
                    "device_id": device_id,
                    "display_name": display_name,
                },
            )
            response.raise_for_status()
            return response.json()

    def list_devices(self) -> dict[str, Any]:
        with self._client() as client:
            response = client.get("/api/v1/devices", headers=self._headers)
            response.raise_for_status()
            return response.json()

    def rename_device(self, device_id: str, display_name: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.patch(
                f"/api/v1/devices/{device_id}",
                json={"display_name": display_name},
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    def revoke_self(self) -> dict[str, Any]:
        with self._client() as client:
            response = client.post(
                "/api/v1/devices/self/revoke",
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    def init_transfer(
        self,
        manifest: dict[str, Any],
        bundle_path: Path,
        bundle_sha256: str,
        expires_in_hours: int = 24,
    ) -> dict[str, Any]:
        payload = {
            "transfer_id": manifest["transfer_id"],
            "sender_device_id": manifest["sender_device_id"],
            "receiver_device_id": manifest["receiver_device_id"],
            "file_count": manifest["file_count"],
            "total_size": manifest["total_size"],
            "bundle_size": bundle_path.stat().st_size,
            "bundle_sha256": bundle_sha256,
            "expires_in_hours": expires_in_hours,
            "manifest": manifest,
        }
        with self._client() as client:
            response = client.post(
                "/api/v1/transfers/init",
                json=payload,
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    def upload_transfer(
        self,
        manifest: dict[str, Any],
        bundle_path: Path,
        bundle_sha256: str,
    ) -> dict[str, Any]:
        initialized = self.init_transfer(manifest, bundle_path, bundle_sha256)
        if initialized.get("status") in {"AVAILABLE", "ACCEPTED", "COMPLETED"}:
            return initialized
        with bundle_path.open("rb") as file_obj:
            with self._client() as client:
                response = client.put(
                    f"/api/v1/transfers/{manifest['transfer_id']}/bundle",
                    content=file_obj,
                    headers={**self._headers, "Content-Type": "application/gzip"},
                )
                response.raise_for_status()
                return response.json()

    def list_transfer_inbox(self, limit: int = 100) -> dict[str, Any]:
        with self._client() as client:
            response = client.get(
                "/api/v1/transfers/inbox",
                params={"limit": limit},
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    def get_transfer_metadata(self, transfer_id: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.get(
                f"/api/v1/transfers/{transfer_id}",
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    def download_transfer_manifest(self, transfer_id: str) -> dict[str, Any]:
        with self._client() as client:
            response = client.get(
                f"/api/v1/transfers/{transfer_id}/manifest",
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    def update_transfer_status(self, transfer_id: str, action: str) -> dict[str, Any]:
        if action not in {"accept", "complete", "reject"}:
            raise ValueError(f"Unsupported transfer action: {action}")
        with self._client() as client:
            response = client.post(
                f"/api/v1/transfers/{transfer_id}/{action}",
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    def download_transfer_bundle(self, transfer_id: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = destination.with_name(destination.name + ".downloading")
        with self._client() as client:
            with client.stream(
                "GET",
                f"/api/v1/transfers/{transfer_id}/bundle",
                headers=self._headers,
            ) as response:
                response.raise_for_status()
                with temp_path.open("wb") as file_obj:
                    for chunk in response.iter_bytes():
                        if chunk:
                            file_obj.write(chunk)
        temp_path.replace(destination)
        return destination

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

def upload_with_retry(
    server: ServerSection,
    manifest: dict,
    bundle_path: Path,
    bundle_sha256: str,
    *,
    retry_count: int,
    retry_interval_seconds: float,
    client_factory: Callable[[ServerSection], BackupServerClient] = BackupServerClient,
    sleep: Callable[[float], None] = time.sleep,
) -> DestinationUploadResult:
    attempts = 0
    last_error: str | None = None
    total_attempts = max(1, retry_count + 1)
    for attempt in range(total_attempts):
        attempts += 1
        try:
            response = client_factory(server).upload_backup(
                manifest,
                bundle_path,
                bundle_sha256,
            )
            if response.get("status") != "COMPLETED":
                raise RuntimeError(f"Unexpected upload status: {response.get('status')}")
            remote_sha256 = response.get("bundle_sha256")
            if remote_sha256 and remote_sha256 != bundle_sha256:
                raise ValueError(
                    f"Server {server.id} returned a different bundle SHA256"
                )
            return DestinationUploadResult(
                server_id=server.id,
                server_name=server.name,
                base_url=server.base_url,
                status="COMPLETED",
                attempts=attempts,
                bundle_sha256=remote_sha256 or bundle_sha256,
            )
        except Exception as exc:
            last_error = str(exc)
            if attempt + 1 < total_attempts and retry_interval_seconds > 0:
                sleep(retry_interval_seconds)
    return DestinationUploadResult(
        server_id=server.id,
        server_name=server.name,
        base_url=server.base_url,
        status="FAILED",
        attempts=attempts,
        error_message=last_error,
    )


def list_backups_across_servers(
    config: AppConfig,
    *,
    server_id: str | None = None,
    machine_id: str | None = None,
    task_name: str | None = None,
    limit: int = 50,
    offset: int = 0,
    server_clients: dict[str, BackupServerClient] | None = None,
) -> dict[str, Any]:
    servers = (
        [config.get_server(server_id)]
        if server_id and server_id != "all"
        else config.enabled_servers()
    )
    merged: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for server in servers:
        try:
            client = (server_clients or {}).get(server.id) or BackupServerClient(server)
            response = client.list_backups(
                machine_id=machine_id,
                task_name=task_name,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            errors.append({"server_id": server.id, "error": str(exc)})
            continue
        for item in response.get("items", []):
            backup_id = str(item["backup_id"])
            entry = merged.setdefault(
                backup_id,
                {
                    **item,
                    "copies": [],
                    "copy_status": "AVAILABLE",
                },
            )
            entry["copies"].append(
                {
                    "server_id": server.id,
                    "server_name": server.name,
                    "base_url": server.base_url,
                    "status": item.get("status"),
                    "bundle_sha256": item.get("bundle_sha256"),
                }
            )

    for entry in merged.values():
        hashes = {
            copy.get("bundle_sha256")
            for copy in entry["copies"]
            if copy.get("bundle_sha256")
        }
        if len(hashes) > 1:
            entry["copy_status"] = "CONFLICT"
        elif len(entry["copies"]) < len(servers):
            entry["copy_status"] = "DEGRADED"
        else:
            entry["copy_status"] = "HEALTHY"

    items = sorted(
        merged.values(),
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("backup_id") or "")),
        reverse=True,
    )
    return {
        "items": items,
        "total": len(items),
        "servers_checked": [server.id for server in servers],
        "server_errors": errors,
    }
