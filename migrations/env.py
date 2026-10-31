"""Alembic environment. The target database is app.config.DATABASE_URL unless a URL is passed
programmatically (scripts/init_db.py and the tests do that via config.attributes['url'])."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app import config as app_config
from app import models  # noqa: F401  (register tables on Base.metadata)
from app.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)
target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("url") or app_config.DATABASE_URL


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
