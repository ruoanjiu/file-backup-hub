from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

from client.app.config import load_config
from client.app.transfer import (
    prepare_transfer,
    receive_transfer,
    reject_transfer,
    send_transfer,
)
from client.app.utils.hashing import calculate_sha256
from client.tests.test_client_backup import write_config


class FakeTransferClient:
    def __init__(self, manifest: dict | None = None, bundle_path: Path | None = None) -> None:
        self.manifest = manifest
        self.bundle_path = bundle_path
        self.actions: list[str] = []

    def upload_transfer(self, manifest: dict, bundle_path: Path, bundle_sha256: str) -> dict:
        assert bundle_path.is_file()
        assert calculate_sha256(bundle_path) == bundle_sha256
        self.manifest = manifest
        self.bundle_path = bundle_path
        return {"transfer_id": manifest["transfer_id"], "status": "AVAILABLE"}

    def get_transfer_metadata(self, transfer_id: str) -> dict:
        assert self.manifest is not None and self.bundle_path is not None
        return {
            "transfer_id": transfer_id,
            "status": "AVAILABLE",
            "bundle_sha256": calculate_sha256(self.bundle_path),
        }

    def download_transfer_manifest(self, transfer_id: str) -> dict:
        assert self.manifest is not None
        return self.manifest

    def update_transfer_status(self, transfer_id: str, action: str) -> dict:
        self.actions.append(action)
        statuses = {
            "accept": "ACCEPTED",
            "complete": "COMPLETED",
            "reject": "REJECTED",
        }
        return {"transfer_id": transfer_id, "status": statuses[action]}

    def download_transfer_bundle(self, transfer_id: str, destination: Path) -> Path:
        assert self.bundle_path is not None
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.bundle_path, destination)
        return destination


def make_transfer_config(tmp_path: Path, machine_id: str) -> tuple[Path, object]:
    backup_source = tmp_path / f"backup-source-{machine_id}"
    backup_source.mkdir()
    client_root = tmp_path / machine_id
    client_root.mkdir()
    config_path = write_config(client_root, backup_source)
    raw = config_path.read_text(encoding="utf-8")
    raw = raw.replace('machine_id: "office-pc-01"', f'machine_id: "{machine_id}"')
    raw += f'''\ntransfer:
  inbox_dir: "{(tmp_path / machine_id / 'inbox').as_posix()}"
  temp_dir: "{(tmp_path / machine_id / 'transfer-tmp').as_posix()}"
  allowed_send_roots:
    - "{(tmp_path / 'send-root').as_posix()}"
  require_confirmation: true
  overwrite_existing: false
'''
    config_path.write_text(raw, encoding="utf-8")
    return config_path, load_config(config_path)


def test_prepare_send_and_receive_transfer_without_changing_sources(tmp_path: Path) -> None:
    send_root = tmp_path / "send-root"
    (send_root / "nested").mkdir(parents=True)
    (send_root / "a.txt").write_text("hello", encoding="utf-8")
    (send_root / "nested" / "b.json").write_text('{"ok": true}', encoding="utf-8")
    source_hashes = {
        path: calculate_sha256(path)
        for path in send_root.rglob("*")
        if path.is_file()
    }
    _, sender_config = make_transfer_config(tmp_path, "sender-pc")
    _, receiver_config = make_transfer_config(tmp_path, "receiver-pc")

    prepared = prepare_transfer(sender_config, [send_root], "receiver-pc")
    assert prepared.file_count == 2
    assert prepared.bundle_path.is_file()
    with tarfile.open(prepared.bundle_path, "r:gz") as tar:
        names = sorted(tar.getnames())
        assert names == [
            "files/send-root/a.txt",
            "files/send-root/nested/b.json",
            "manifest.json",
        ]
        bundled_manifest = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
        assert bundled_manifest["receiver_device_id"] == "receiver-pc"
    assert {path: calculate_sha256(path) for path in source_hashes} == source_hashes

    fake = FakeTransferClient()
    sent = send_transfer(
        sender_config,
        [send_root],
        "receiver-pc",
        server_id="server-1",
        server_clients={"server-1": fake},
        cleanup=False,
    )
    assert sent.status == "AVAILABLE"
    assert fake.manifest is not None and fake.bundle_path is not None

    destination = tmp_path / "received"
    (destination / "send-root").mkdir(parents=True)
    received = receive_transfer(
        receiver_config,
        str(fake.manifest["transfer_id"]),
        server_id="server-1",
        destination=destination,
        server_client=fake,
    )
    assert received.status == "COMPLETED"
    assert received.received_count == 2
    assert (destination / "send-root (1)" / "a.txt").read_text(encoding="utf-8") == "hello"
    assert json.loads(
        (destination / "send-root (1)" / "nested" / "b.json").read_text(encoding="utf-8")
    ) == {"ok": True}
    assert fake.actions == ["accept", "complete"]
    assert {path: calculate_sha256(path) for path in source_hashes} == source_hashes


def test_transfer_can_target_server_and_receiver_can_reject(tmp_path: Path) -> None:
    send_root = tmp_path / "send-root"
    send_root.mkdir()
    (send_root / "server.txt").write_text("to server", encoding="utf-8")
    _, config = make_transfer_config(tmp_path, "sender-pc")
    fake = FakeTransferClient()

    sent = send_transfer(
        config,
        [send_root / "server.txt"],
        "__server__",
        server_id="server-1",
        server_clients={"server-1": fake},
        cleanup=False,
    )
    assert sent.status == "AVAILABLE"
    assert fake.manifest["receiver_device_id"] == "__server__"

    rejected = reject_transfer(
        config,
        str(fake.manifest["transfer_id"]),
        server_id="server-1",
        server_client=fake,
    )
    assert rejected["status"] == "REJECTED"
    assert fake.actions == ["reject"]
