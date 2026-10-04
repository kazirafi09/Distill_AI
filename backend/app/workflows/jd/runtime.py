from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import get_settings
from app.workflows.jd.graph import build_jd_graph


@contextmanager
def open_jd_graph():
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

        graph = build_jd_graph(checkpointer)
        yield graph