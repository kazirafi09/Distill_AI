import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


logger = logging.getLogger("distill_ai.errors")


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.error(
            "Unexpected error | error_type=%s",
            type(exc).__name__,
        )

        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )