"""Server-side sessions. The browser holds only an opaque random token in an httpOnly cookie;
the database stores an HMAC of it, an absolute expiry, an idle timeout and a CSRF token."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import utcnow
from app.models import AuthSession, User


def _key() -> bytes:
    return (config.SECRET_KEY or "dev-only-insecure-key-change-me").encode()


def hash_token(token: str) -> str:
    return hmac.new(_key(), token.encode(), hashlib.sha256).hexdigest()


def create_session(db: Session, user: User, ip: str | None, user_agent: str | None) -> tuple[str, AuthSession]:
    """Always mint a NEW token at login (prevents session fixation)."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    s = AuthSession(token_hash=hash_token(token), user_id=user.user_code, csrf_token=secrets.token_urlsafe(24), created_at=now,
                    last_seen_at=now, expires_at=now + timedelta(minutes=config.SESSION_ABSOLUTE_MINUTES), ip=(ip or "")[:64],
                    user_agent=(user_agent or "")[:200])
    db.add(s)
    db.flush()
    return token, s


def resolve_session(db: Session, token: str | None) -> tuple[AuthSession, User] | None:
    if not token:
        return None
    s = db.execute(select(AuthSession).where(AuthSession.token_hash == hash_token(token))).scalar_one_or_none()
    if s is None or s.revoked_at is not None:
        return None
    now = utcnow()
    if now >= s.expires_at or now - s.last_seen_at > timedelta(minutes=config.SESSION_IDLE_MINUTES):
        s.revoked_at = now                         # expired sessions are retired, never revived
        db.flush()
        return None
    user = db.execute(select(User).where(User.user_code == s.user_id)).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    if (now - s.last_seen_at).total_seconds() > 30:
        s.last_seen_at = now
        db.flush()
    return s, user


def revoke_session(db: Session, s: AuthSession) -> None:
    s.revoked_at = utcnow()
    db.flush()


def revoke_all_for_user(db: Session, user_code: str) -> int:
    n = 0
    for s in db.execute(select(AuthSession).where(AuthSession.user_id == user_code, AuthSession.revoked_at.is_(None))).scalars():
        s.revoked_at = utcnow(); n += 1
    db.flush()
    return n
