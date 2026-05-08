from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _sqlite_path_from_url(database_url: str) -> Path | None:
    if database_url in {"sqlite://", "sqlite:///:memory:"}:
        return None
    if database_url.startswith("sqlite:///"):
        raw_path = database_url.removeprefix("sqlite:///")
        if raw_path == ":memory:":
            return None
        return Path(raw_path)
    return None


def configure_database(database_url: str) -> None:
    global _engine, _session_factory

    sqlite_path = _sqlite_path_from_url(database_url)
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    _engine = create_engine(database_url, connect_args=connect_args, future=True)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)


def get_engine() -> Engine:
    if _engine is None:
        configure_database("sqlite:///./data/app.sqlite")
    if _engine is None:
        raise RuntimeError("database engine is not configured")
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        configure_database("sqlite:///./data/app.sqlite")
    if _session_factory is None:
        raise RuntimeError("database session factory is not configured")
    return _session_factory


def init_db() -> None:
    from server.app import models  # noqa: F401

    Base.metadata.create_all(bind=get_engine())


def get_db() -> Generator[Session, None, None]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
