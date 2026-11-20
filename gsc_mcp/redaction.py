"""Redact access tokens and email addresses from every log record."""

from __future__ import annotations

import logging
import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_BEARER = re.compile(r"(?i)\bBearer\s+\S+")
_GOOGLE_TOKEN = re.compile(r"\bya29\.[0-9A-Za-z_\-]+")
_ACCESS_TOKEN_PARAM = re.compile(r"(?i)(access_token=)[^&\s]+")
_AUTHORIZATION = re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*)\S+")

_INSTALLED = False


def redact_text(value: str) -> str:
    """Remove tokens and email addresses from a log string."""
    value = _BEARER.sub("Bearer [redacted]", value)
    value = _GOOGLE_TOKEN.sub("[redacted]", value)
    value = _ACCESS_TOKEN_PARAM.sub(r"\1[redacted]", value)
    value = _AUTHORIZATION.sub(r"\1[redacted]", value)
    value = _EMAIL.sub("[redacted]", value)
    return value


def install_redaction() -> None:
    """Install a process-wide log-record factory. Safe to call more than once."""
    global _INSTALLED
    if _INSTALLED:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_text(item) if isinstance(item, str) else item
                for item in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_text(item) if isinstance(item, str) else item
                for key, item in record.args.items()
            }
        return record

    logging.setLogRecordFactory(factory)
    for name in (
        "httpx",
        "httpx2",
        "httpcore",
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
    _INSTALLED = True
