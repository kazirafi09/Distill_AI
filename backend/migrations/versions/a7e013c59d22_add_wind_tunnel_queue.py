"""Durable Wind Tunnel approval outbox and worker leases."""
from alembic import op
import sqlalchemy as sa

revision = "a7e013c59d22"
down_revision = "f6d921e3c845"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("wind_tunnel_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("job_descriptions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False),
        sa.Column("source_version", sa.String(64), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("wind_tunnel_runs.id", ondelete="SET NULL")),
        sa.Column("claim", sa.String(36)), sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("error_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("job_id", "request_key", name="uq_wind_tunnel_task_request"),
        sa.CheckConstraint("trigger IN ('approval', 'more')", name="ck_wind_tunnel_task_trigger"),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'limited', 'failed', 'cancelled', 'stale')", name="ck_wind_tunnel_task_status"))
    for column in ("owner_id", "job_id", "status"):
        op.create_index(f"ix_wind_tunnel_tasks_{column}", "wind_tunnel_tasks", [column])


def downgrade():
    op.drop_table("wind_tunnel_tasks")
