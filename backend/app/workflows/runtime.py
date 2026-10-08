from contextlib import contextmanager
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import get_settings


@contextmanager
def open_workflow_checkpointer():
    settings = get_settings()

    connection_info = make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        connect_timeout=5,
    )

    with ConnectionPool(
        conninfo=connection_info,
        min_size=1,
        max_size=2,
        timeout=5,
        open=True,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "prepare_threshold": 0,
        },
    ) as pool:
        pool.wait(timeout=5)

        checkpointer = PostgresSaver(pool)
        checkpointer.setup()

        yield checkpointer


@asynccontextmanager
async def open_async_workflow_checkpointer():
    """Same PostgreSQL checkpoint tables/configuration as existing workflows."""
    settings = get_settings()
    connection_info = make_conninfo(host=settings.postgres_host, port=settings.postgres_port,
        dbname=settings.postgres_db, user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(), connect_timeout=5)
    async with AsyncConnectionPool(conninfo=connection_info, min_size=1, max_size=2,
        open=False, kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": 0}) as pool:
        await pool.wait(timeout=5)
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        yield checkpointer


@contextmanager
def open_workflow_graphs():
    from app.workflows.assistant.graph import build_assistant_graph
    from app.workflows.jd.graph import build_jd_graph

    with open_workflow_checkpointer() as checkpointer:
        jd_graph = build_jd_graph(checkpointer)
        assistant_graph = build_assistant_graph(checkpointer)

        yield jd_graph, assistant_graph
