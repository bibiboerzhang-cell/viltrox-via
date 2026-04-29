from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _load_layered_env() -> None:
    root = Path(__file__).resolve().parents[1]
    base = root / ".env"
    if base.exists():
        load_dotenv(base, override=False)

    environment = os.getenv("ENVIRONMENT", "local").strip().lower()
    profile = root / f".env.{environment}"
    if profile.exists():
        load_dotenv(profile, override=True)

    explicit = os.getenv("ENV_FILE", "").strip()
    if explicit:
        explicit_path = Path(explicit)
        if explicit_path.exists():
            load_dotenv(explicit_path, override=True)


def _resolve_database_url() -> str:
    _load_layered_env()
    return (
        os.getenv("DATABASE_POOL_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
        or f"sqlite:///{Path(__file__).resolve().parents[1] / 'submissions.db'}"
    )


def run_migrations_offline() -> None:
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        _resolve_database_url(),
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
