"""Run durable five-minute Drive checks independently of the browser."""
import asyncio
import logging
from app.services.cv.source_checks import run_due_checks


async def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            checked = await run_due_checks()
            if checked:
                logging.getLogger("distill_ai.cv_source_checks").info("Checked %s CV sources", checked)
        except Exception as error:
            logging.getLogger("distill_ai.cv_source_checks").warning(
                "CV check worker pass failed | error_type=%s", type(error).__name__)
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
