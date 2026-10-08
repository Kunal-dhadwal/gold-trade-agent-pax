"""Structured logging with redaction for API keys and secrets."""

import logging
import re
import sys
from typing import Any, Dict


REDACTION_PATTERNS = [
    re.compile(r"(api[_-]?key[\"'\s:=]+)([\w\-]+)", re.IGNORECASE),
    re.compile(r"(secret[\"'\s:=]+)([\w\-]+)", re.IGNORECASE),
    re.compile(r"(password[\"'\s:=]+)([\w\-]+)", re.IGNORECASE),
    re.compile(r"(authorization[\"'\s:=]+Bearer\s+)([\w\.\-]+)", re.IGNORECASE),
]


def redact_sensitive_data(message: str) -> str:
    """Mask sensitive keys and secrets in log messages."""
    redacted = message
    for pattern in REDACTION_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted


class SafeFormatter(logging.Formatter):
    """Custom log formatter ensuring no raw secrets are printed."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return redact_sensitive_data(original)


def setup_logger(name: str = "gold_quant", level: str = "INFO") -> logging.Logger:
    """Initialize and return a configured safe logger."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        formatter = SafeFormatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


logger = setup_logger()
