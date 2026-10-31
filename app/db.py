"""SQLAlchemy engine/session. SQLite by default; any SQLAlchemy URL (e.g. PostgreSQL) works unchanged."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import config


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC (SQLite has no timezone type); always interpret stored datetimes as UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def init_engine(url: str | None = None) -> Engine:
    global engine, SessionLocal
    url = url or config.DATABASE_URL
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
    if engine is not None:
        engine.dispose()
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        # Documented SQLAlchemy recipe so SAVEPOINT / nested transactions behave correctly with pysqlite.
        @event.listens_for(engine, "connect")
        def _connect(dbapi_conn, _):
            dbapi_conn.isolation_level = None                  # we emit BEGIN ourselves (see _begin)
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            if "memory" not in url:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        @event.listens_for(engine, "begin")
        def _begin(conn):
            # IMMEDIATE takes SQLite's single write lock up front, so two concurrent writers QUEUE (up to the busy timeout)
            # instead of one failing with "database is locked" when a deferred read transaction tries to upgrade. This is what
            # makes the approval compare-and-set safe under concurrency. (Not needed, and not used, on PostgreSQL.)
            conn.exec_driver_sql("BEGIN IMMEDIATE")
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    return engine


def get_engine() -> Engine:
    return engine or init_engine()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception."""
    get_engine()
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Routes commit explicitly through services; this closes the session."""
    get_engine()
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
