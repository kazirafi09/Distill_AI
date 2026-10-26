"""Check and import Drive/Sheet applications every one minute without a browser."""
import asyncio
import logging
from app.core.logging import setup_logging
import app.models.user  # Register the users table referenced by candidate/identity writes.
from app.services.cv.source_checks import run_due_checks


async def main():
    setup_logging("cv-source-check-worker")
    while True:
        try:
            checked = await run_due_checks()
            if checked:
                logging.getLogger("distill_ai.cv_source_checks").info("Checked/imported %s CV sources", checked)
        except Exception as error:
            logging.getLogger("distill_ai.cv_source_checks").warning(
                "CV check worker pass failed | error_type=%s", type(error).__name__)
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
