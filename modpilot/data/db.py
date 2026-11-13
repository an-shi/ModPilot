from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .models import Base

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class Database:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = str(db_path)
        is_memory = self.db_path == ":memory:"
        url = "sqlite:///:memory:" if is_memory else f"sqlite:///{Path(self.db_path).as_posix()}"
        if not is_memory:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {
            "echo": False,
            "future": True,
            "connect_args": {"check_same_thread": False, "timeout": 30},
        }
        if is_memory:
            kwargs["poolclass"] = StaticPool
        self.engine: Engine = create_engine(url, **kwargs)
        self._configure_sqlite(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    @staticmethod
    def _configure_sqlite(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def _set_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=30000")
            finally:
                cursor.close()

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            current = connection.execute(text("PRAGMA user_version")).scalar_one()
            if current < SCHEMA_VERSION:
                connection.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
        log.info("数据库已就绪：%s", self.db_path)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
