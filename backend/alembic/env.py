"""Alembic environment. URL and schema come from app settings (env vars / backend/.env)."""
import os
from logging.config import fileConfig

from sqlalchemy import text

from alembic import context
from app.core.config import settings
from app.core.database import make_engine
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DB_SCHEMA can be overridden per run (tests, scratch autogenerate).
SCHEMA = os.environ.get("DB_SCHEMA", settings.DB_SCHEMA)
MASTER_SCHEMA = settings.MASTER_SCHEMA


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001, ARG001
    """Never create, alter or drop FAR's shared master tables (they belong to FAR)."""
    if type_ == "table" and (obj.schema == MASTER_SCHEMA or obj.info.get("owned_by") == "FAR"):
        return False
    if type_ == "table" and name == "alembic_version":
        return False
    return True
# ConfigParser treats % as interpolation, so escape it (URL-encoded passwords contain %).
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(SCHEMA)
    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = :s"), {"s": SCHEMA}
        ).scalar()
        if not exists:
            # Only when missing: in company_db the schema pre-exists and the portal login
            # has rights inside it but not CREATE on the database.
            connection.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
        # End the check's implicit transaction so Alembic owns (and commits) the migration.
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table_schema=SCHEMA,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
