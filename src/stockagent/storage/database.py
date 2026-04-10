from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from stockagent.config import get_settings
from stockagent.storage.models import Base

FALLBACK_SQLITE_URL = "sqlite:///./stockagent.db"


def build_engine(database_url: str | None = None) -> Engine:
    settings = get_settings()
    return create_engine(database_url or settings.database_url, future=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    primary_engine = build_engine()
    try:
        with primary_engine.connect():
            return primary_engine
    except SQLAlchemyError:
        return build_engine(FALLBACK_SQLITE_URL)


SessionLocal = sessionmaker(autoflush=False, autocommit=False, future=True)


def init_database() -> None:
    engine = get_engine()
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)
    _apply_lightweight_migrations(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    if SessionLocal.kw.get("bind") is None:
        init_database()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _apply_lightweight_migrations(engine: Engine) -> None:
    inspector = inspect(engine)
    if "daily_reports" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("daily_reports")}
    with engine.begin() as connection:
        if "metadata_json" not in columns:
            connection.execute(text("ALTER TABLE daily_reports ADD COLUMN metadata_json JSON"))
        if "context_json" not in columns:
            connection.execute(text("ALTER TABLE daily_reports ADD COLUMN context_json JSON"))
