"""Bounded per-user logs with best-effort redaction of common secrets."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import re

from .app_settings import user_data_dir

_SECRET = re.compile(r"(?i)(password|passwd|token|api[_-]?key|secret)\s*[:=]\s*([^\s,;]+)")


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        original = record.msg
        args = record.args
        try:
            record.msg = _SECRET.sub(r"\1=[REDACTED]", str(record.getMessage()))
            record.args = ()
            return super().format(record)
        finally:
            record.msg, record.args = original, args


def configure_logging(directory=None, *, logger_name="invoiceflow"):
    log_dir = directory or user_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    target = str(log_dir / "invoiceflow.log")
    if not any(getattr(handler, "baseFilename", None) == target for handler in logger.handlers):
        handler = RotatingFileHandler(target, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return logger
