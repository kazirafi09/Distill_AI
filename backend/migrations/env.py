import asyncio
from logging.config import fileConfig

import selectors
import sys

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.base import Base
from app.models.user import User
from app.models.jd import JDConversation, JobDescription
from app.models.jd_reference import JDReference
from app.models.assistant import AssistantConversation
from app.models.cv import CVSource, CVDocument, CVFileVersion
from app.models.cv_parsing import CVParseResult
from app.models.cv_screening import CVIndex, ScreeningRun, CVAssessment

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
target_metadata = Base.metadata

def include_name(name, type_, parent_names):
    if type_ == "table" and name is not None:
        return not (
            name == "checkpoints"
            or name.startswith("checkpoint_")
        )

    return True

def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    migration_engine = create_async_engine(
        settings.database_url,
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 5},
    )

    try:
        async with migration_engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await migration_engine.dispose()


def run_migrations_online() -> None:
    if sys.platform == "win32":
        asyncio.run(
            run_async_migrations(),
            loop_factory=lambda: asyncio.SelectorEventLoop(
                selectors.SelectSelector()
            ),
        )
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
