"""Python logging with a redaction filter: nothing that looks like a secret reaches a log line."""
from __future__ import annotations

import logging

from app import config
from app.security.redaction import redact_secrets


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact_secrets(record.getMessage())[0]
            record.args = ()
        except Exception:                                    # never let logging fail the request
            pass
        return True


class RedactingFormatter(logging.Formatter):
    """Redacts the FINAL text, including exception tracebacks (driver errors often embed DSNs with passwords)."""
    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))[0]


def setup_logging() -> logging.Logger:
    log = logging.getLogger("campus")
    if not log.handlers:
        h = logging.StreamHandler()
        h.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        h.addFilter(RedactingFilter())
        log.addHandler(h)
        log.propagate = False
    log.setLevel(config.LOG_LEVEL)
    return log


logger = setup_logging()
