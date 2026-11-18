"""Engine and session factory. The schema is pinned via search_path."""

import re
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def make_engine(schema: str = settings.DB_SCHEMA):
    for name in (schema, settings.MASTER_SCHEMA):
        if not _SCHEMA_RE.match(name):
            raise ValueError(f"Invalid schema name: {name!r}")
    # Portal tables resolve through search_path; FAR's shared masters are always
    # schema-qualified (MASTER_SCHEMA) so the two can never be confused.
    return create_engine(
        settings.sqlalchemy_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        # UTC session time zone: every timestamp leaves the API as UTC with offset.
        connect_args={"options": f"-csearch_path={schema} -ctimezone=UTC"},
    )


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
