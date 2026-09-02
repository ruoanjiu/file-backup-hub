from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from server.app.auth import Actor
from server.app.database import Base
from server.app.models import Client
from server.app.services.device_service import remove_revoked_device, revoke_device


def test_service_revoke_soft_disables_existing_client_without_deleting_row(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'service.sqlite'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        client = Client(
            machine_id="service-pc",
            display_name="服务层测试设备",
            token_hash="dummy-token-hash",
            enabled=1,
            created_at="2026-09-01T00:00:00+00:00",
            updated_at="2026-09-01T00:00:00+00:00",
            last_seen_at="2026-09-01T00:00:00+00:00",
        )
        db.add(client)
        db.commit()
        original_id = client.id

        result = revoke_device(
            db,
            Actor(actor_id="admin", machine_id=None, is_admin=True),
            machine_id="service-pc",
            admin_only=True,
        )

        rows = db.scalars(
            select(Client).where(Client.machine_id == "service-pc")
        ).all()
        assert result.id == original_id
        assert result.enabled == 0
        assert len(rows) == 1
        assert rows[0].id == original_id
        assert rows[0].token_hash == "dummy-token-hash"

        removed_id = remove_revoked_device(
            db,
            Actor(actor_id="admin", machine_id=None, is_admin=True),
            machine_id="service-pc",
        )
        assert removed_id == "service-pc"
        assert db.scalar(
            select(Client).where(Client.machine_id == "service-pc")
        ) is None
