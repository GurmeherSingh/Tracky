import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

LOG_PATH = Path("logs/tracker.log")
MAX_BYTES = 2_000_000
BACKUPS = 3


def configure_logging(path: Path | None = LOG_PATH) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if path is not None:
        # stdout dies with the terminal; diagnosing an overnight failure needs
        # the lines that were written before the restart
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=handlers, force=True)
    # per-request client chatter would drown the events we actually emit
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        # without this structlog prints straight to stdout and no stdlib
        # handler — including the file one above — ever sees a line
        logger_factory=structlog.stdlib.LoggerFactory(),
    )


def get_logger(**bound):
    return structlog.get_logger().bind(**bound)
