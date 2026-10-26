import logging
import re
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(service_name: str = "backend", *, logs_dir: Path | None = None) -> None:
    """One rotating text file per service, shared by its application loggers."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", service_name):
        raise ValueError("Invalid log service name")
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logs_dir = logs_dir or Path(__file__).resolve().parents[2] / "logs"
    log_path = (logs_dir / f"{service_name}.txt").resolve()
    if any(getattr(handler, "_distill_log_path", None) == str(log_path) for handler in logger.handlers):
        return
    logs_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S UTC",
    )
    formatter.converter = time.gmtime
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    # Only replace our own handlers; preserve test runners/host integrations.
    for handler in list(logger.handlers):
        if getattr(handler, "_distill_owned", False):
            logger.removeHandler(handler)
            handler.close()
    for handler in (console_handler, file_handler):
        handler._distill_owned = True
        handler._distill_log_path = str(log_path)
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logging.getLogger("distill_ai").setLevel(logging.INFO)
    logging.getLogger("distill_ai").propagate = True
    # Uvicorn otherwise keeps access/error records on separate console handlers.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        server_logger = logging.getLogger(name)
        server_logger.handlers.clear()
        server_logger.propagate = True
    logging.getLogger("distill_ai.logging").info("Logging initialized | service=%s", service_name)
