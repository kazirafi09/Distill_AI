"""Use descriptive post-interview run kinds and migrate their metadata consistently."""
from copy import deepcopy
import hashlib
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "cb0472a19e36"
down_revision = "ca9174e803b2"
branch_labels = None
depends_on = None

LEGACY_NAMES = {"f8": "cv_claim_review", "f9": "interview_performance", "f10": "interview_quality"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def renamed_metadata(kind, job_id, inputs, artifact, names):
    """Rename only machine metadata; never replace text inside candidate evidence."""
    inputs, artifact = deepcopy(inputs), deepcopy(artifact)
    new_kind = names[kind]
    inputs.pop("source_key", None)
    key = digest({"kind": new_kind, "job_id": str(job_id), **inputs})
    inputs["source_key"] = key
    if artifact is not None:
        if "kind" in artifact:
            artifact["kind"] = new_kind
        if "provenance" in artifact:
            artifact["provenance"]["input_key"] = key
        if "artifact_sha256" in artifact:
            artifact.pop("artifact_sha256")
            artifact["artifact_sha256"] = digest(artifact)
    return new_kind, key, inputs, artifact


def migrate(names):
    connection = op.get_bind()
    runs = sa.table("assessment_runs", sa.column("id", sa.Uuid()), sa.column("job_id", sa.Uuid()),
        sa.column("kind", sa.String()), sa.column("input_key", sa.String()), sa.column("inputs", JSONB()),
        sa.column("error_code", sa.String()), sa.column("status", sa.String()))
    results = sa.table("assessment_results", sa.column("run_id", sa.Uuid()), sa.column("payload", JSONB()))
    reviews = sa.table("assessment_finding_reviews", sa.column("id", sa.Uuid()),
        sa.column("run_id", sa.Uuid()), sa.column("payload", JSONB()))
    for row in connection.execute(sa.select(runs).where(runs.c.kind.in_(names))).mappings().all():
        artifact = connection.execute(sa.select(results.c.payload).where(results.c.run_id == row["id"])).scalar_one_or_none()
        if row["status"] == "completed" and (not artifact or artifact.get("artifact_sha256") !=
                digest({key:value for key,value in artifact.items() if key != "artifact_sha256"})):
            raise RuntimeError("Cannot rename a completed assessment whose saved integrity check fails.")
        kind, key, inputs, artifact = renamed_metadata(row["kind"], row["job_id"], row["inputs"], artifact, names)
        code = row["error_code"]
        if code and code.startswith(row["kind"].upper() + "_"):
            code = kind.upper() + code[len(row["kind"]):]
        connection.execute(runs.update().where(runs.c.id == row["id"]).values(kind=kind,input_key=key,inputs=inputs,error_code=code))
        if artifact is not None:
            error = artifact.get("error_code")
            if error and error.startswith(row["kind"].upper() + "_"):
                artifact["error_code"] = kind.upper() + error[len(row["kind"]):]
                if "artifact_sha256" in artifact:
                    artifact.pop("artifact_sha256")
                    artifact["artifact_sha256"] = digest(artifact)
            connection.execute(results.update().where(results.c.run_id == row["id"]).values(payload=artifact))
            for review in connection.execute(sa.select(reviews).where(reviews.c.run_id == row["id"])).mappings().all():
                payload = deepcopy(review["payload"])
                if "artifact_sha256" in payload and "artifact_sha256" in artifact:
                    payload["artifact_sha256"] = artifact["artifact_sha256"]
                    connection.execute(reviews.update().where(reviews.c.id == review["id"]).values(payload=payload))


def upgrade():
    op.drop_constraint("ck_assessment_run_kind", "assessment_runs", type_="check")
    op.alter_column("assessment_runs", "kind", existing_type=sa.String(8), type_=sa.String(32), existing_nullable=False)
    migrate(LEGACY_NAMES)
    op.create_check_constraint("ck_assessment_run_kind", "assessment_runs",
        "kind IN ('f5', 'f6', 'cv_claim_review', 'interview_performance', 'interview_quality')")


def downgrade():
    op.drop_constraint("ck_assessment_run_kind", "assessment_runs", type_="check")
    migrate({value: key for key, value in LEGACY_NAMES.items()})
    op.alter_column("assessment_runs", "kind", existing_type=sa.String(32), type_=sa.String(8), existing_nullable=False)
    op.create_check_constraint("ck_assessment_run_kind", "assessment_runs", "kind IN ('f5', 'f6', 'f8', 'f9', 'f10')")
