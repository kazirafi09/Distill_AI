"""Disposable synthetic recruiter/CVs for browser verification; always clean up.

docker compose exec -T backend python tests/cv_ui_check.py create
docker compose exec -T backend python tests/cv_ui_check.py cleanup
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionFactory
from app.models.cv import CVSource
from app.models.user import User
from test_cv_parsing_postgres import PersistenceTests

MARKER = Path("/tmp/distill-cv-ui-check.json")
TEST_PASSWORD = "Synthetic-CV-UI-Check-Only-2026!"


async def main():
    fixture = PersistenceTests("test_model_timeout_retains_text")
    if sys.argv[1] == "create":
        if MARKER.exists():
            raise RuntimeError("Clean up the previous UI fixture first")
        await fixture.asyncSetUp()
        MARKER.write_text(json.dumps({"ids": [str(v) for v in fixture.ids], "users": [str(v) for v in fixture.users],
                                      "paths": [str(p) for p in fixture.paths]}))
        async with SessionFactory() as db:
            owner = await db.get(User, fixture.owner)
            # EmailStr rejects reserved .invalid login domains; example.com is
            # reserved for examples and this address is never contacted.
            owner.email = f"cv-ui-check-{fixture.owner}@example.com"
            owner.password_hash = hash_password(TEST_PASSWORD)
            source = await db.scalar(select(CVSource).where(CVSource.job_description_id == fixture.job_id))
            source.spreadsheet_id, source.sheet_tab_id = f"synthetic-{fixture.job_id}", 0
            await db.commit()
            print(json.dumps({"email": owner.email, "password": TEST_PASSWORD, "job_id": str(fixture.job_id),
                              "file_version_id": str(fixture.ids[0])}))
    elif sys.argv[1] == "cleanup":
        from uuid import UUID
        data = json.loads(MARKER.read_text())
        fixture.ids, fixture.users = [UUID(v) for v in data["ids"]], [UUID(v) for v in data["users"]]
        fixture.paths = [Path(p) for p in data["paths"]]
        await fixture.asyncTearDown()
        MARKER.unlink()
        print("Synthetic UI fixtures removed")
    else:
        raise ValueError("Use create or cleanup")


if __name__ == "__main__":
    asyncio.run(main())
