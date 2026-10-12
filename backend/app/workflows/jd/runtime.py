from contextlib import contextmanager

from app.workflows.jd.graph import build_jd_graph
from app.workflows.runtime import open_workflow_checkpointer


@contextmanager
def open_jd_graph():
    with open_workflow_checkpointer() as checkpointer:
        yield build_jd_graph(checkpointer)