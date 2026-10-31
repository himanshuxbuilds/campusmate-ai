"""FastAPI dependencies: session authentication, CSRF + Origin checks, role requirements, rate limits."""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import AuthSession, User
from app.security import sessions
from app.security.context import Principal
from app.security.ratelimit import limiter
from app.services import audit_service, authorization_service as authz

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def http_error(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def client_ip(request: Request) -> str:
    return (request.client.host if request.client else "unknown")


def check_origin(request: Request) -> None:
    """Defence in depth next to SameSite cookies: a state-changing request carrying an Origin must come from this site."""
    origin = request.headers.get("origin")
    if not origin:
        return
    host = urlparse(origin).netloc
    allowed = {request.headers.get("host", "")} | {urlparse(o).netloc for o in config.ALLOWED_ORIGINS}
    if host not in allowed:
        raise http_error(403, "BAD_ORIGIN", "Cross-site request rejected.")


@dataclass
class Auth:
    principal: Principal
    session: AuthSession
    user: User


def authenticated(request: Request, db: Session = Depends(get_db)) -> Auth:
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    resolved = sessions.resolve_session(db, token)
    if resolved is None:
        if token:
            audit_service.record(db, "SESSION_EXPIRED", "auth.session", "FAILURE", reason="invalid or expired session cookie", commit=True)
        raise http_error(401, "UNAUTHENTICATED", "Please sign in again.")
    sess, user = resolved
    db.commit()                                                   # release the write lock taken by the last_seen touch BEFORE any long work
    if request.method in UNSAFE:
        check_origin(request)
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not hmac.compare_digest(sent, sess.csrf_token):
            audit_service.record(db, "CSRF_REJECTED", "auth.csrf", "DENIED", actor_id=user.user_code, actor_role=user.role, reason="missing or wrong CSRF token", commit=True)
            raise http_error(403, "CSRF_FAILED", "Security check failed. Reload the page and try again.")
    ok, retry = limiter.allow(f"api:{user.user_code}", config.API_RATE_LIMIT_PER_MIN)
    if not ok:
        audit_service.record(db, "RATE_LIMITED", "api.rate_limit", "DENIED", actor_id=user.user_code, actor_role=user.role, reason="api rate limit", commit=True)
        raise HTTPException(status_code=429, detail={"code": "RATE_LIMITED", "message": "Too many requests. Slow down.", "retry_after": retry}, headers={"Retry-After": str(retry)})
    principal = Principal(user_code=user.user_code, name=user.name, role=user.role, programme=user.programme, staff_scope=user.staff_scope or {}, session_id=sess.id)
    request.state.principal = principal
    return Auth(principal, sess, user)


def require_roles(*roles: str):
    def dep(auth: Auth = Depends(authenticated)) -> Auth:
        if auth.principal.role not in roles:
            raise authz.Forbidden(f"route.{roles[0] if len(roles) == 1 else 'restricted'}", "route", None, f"role '{auth.principal.role}' not permitted")
        return auth
    return dep


student_only = require_roles("student")
staff_or_admin = require_roles("staff", "admin")
admin_only = require_roles("admin")
