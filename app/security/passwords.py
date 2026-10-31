from __future__ import annotations

import re

from werkzeug.security import check_password_hash, generate_password_hash

from app import config

_dummy_hash: str | None = None


def hash_password(password: str) -> str:
    return generate_password_hash(password, method=config.PASSWORD_HASH_METHOD)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-ish time: for an unknown user we still verify against a dummy hash so response time does not reveal existence."""
    global _dummy_hash
    if password_hash is None:
        if _dummy_hash is None:
            _dummy_hash = generate_password_hash("not-a-real-password", method=config.PASSWORD_HASH_METHOD)
        check_password_hash(_dummy_hash, password or "")
        return False
    return check_password_hash(password_hash, password or "")


def password_problems(password: str) -> list[str]:
    p = []
    if len(password) < config.PASSWORD_MIN_LENGTH:
        p.append(f"at least {config.PASSWORD_MIN_LENGTH} characters")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        p.append("letters and digits")
    return p
