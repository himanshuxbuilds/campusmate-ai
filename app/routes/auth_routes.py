from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db, utcnow
from app.models import LoginAttempt, User
from app.schemas import LoginBody
from app.security import sessions
from app.security.deps import Auth, authenticated, check_origin, client_ip, http_error
from app.security.passwords import verify_password
from app.services import audit_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
GENERIC = "Invalid username or password."


def _locked(db: Session, username: str, ip: str) -> int:
    """Seconds until the lockout ends (0 = not locked). Counts failures since the last success inside the window."""
    since = utcnow() - timedelta(minutes=config.LOGIN_LOCKOUT_MINUTES)
    last_ok = db.execute(select(func.max(LoginAttempt.created_at)).where(LoginAttempt.username == username, LoginAttempt.success.is_(True))).scalar()
    q = select(func.count()).where(LoginAttempt.username == username, LoginAttempt.success.is_(False), LoginAttempt.created_at >= max(since, last_ok or since))
    fails_user = db.execute(q).scalar_one()
    fails_ip = db.execute(select(func.count()).where(LoginAttempt.ip == ip, LoginAttempt.success.is_(False), LoginAttempt.created_at >= since)).scalar_one()
    if fails_user >= config.LOGIN_MAX_FAILURES or fails_ip >= config.LOGIN_MAX_FAILURES_PER_IP:
        newest = db.execute(select(func.max(LoginAttempt.created_at)).where((LoginAttempt.username == username) | (LoginAttempt.ip == ip), LoginAttempt.success.is_(False))).scalar()
        return max(1, int(config.LOGIN_LOCKOUT_MINUTES * 60 - (utcnow() - (newest or utcnow())).total_seconds()))
    return 0


def user_view(u: User, csrf: str | None = None) -> dict:
    d = {"user_code": u.user_code, "name": u.name, "role": u.role, "programme": u.programme}
    if csrf is not None:
        d["csrf_token"] = csrf
    return d


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)):
    check_origin(request)
    ip, username = client_ip(request), body.username.strip().lower()
    wait = _locked(db, username, ip)
    if wait:
        audit_service.record(db, "LOGIN_LOCKED", "auth.login", "DENIED", actor_id=username[:64], reason="too many failed attempts", commit=True)
        raise HTTPException(status_code=429, detail={"code": "LOGIN_LOCKED", "message": "Too many failed attempts. Try again later.", "retry_after": wait}, headers={"Retry-After": str(wait)})
    user = db.execute(select(User).where(func.lower(User.user_code) == username)).scalar_one_or_none()
    ok = verify_password(body.password, user.password_hash if user else None)        # always hashes, so timing does not reveal existence
    if not (ok and user and user.is_active):
        db.add(LoginAttempt(username=username, ip=ip, success=False))
        audit_service.record(db, "LOGIN_FAILURE", "auth.login", "FAILURE", actor_id=username[:64], reason="invalid credentials", commit=True)
        raise http_error(401, "INVALID_CREDENTIALS", GENERIC)
    db.add(LoginAttempt(username=username, ip=ip, success=True))
    token, sess = sessions.create_session(db, user, ip, request.headers.get("user-agent"))
    old = request.cookies.get(config.SESSION_COOKIE_NAME)
    if old and (prev := sessions.resolve_session(db, old)):
        sessions.revoke_session(db, prev[0])                    # a presented old session never survives a new login
    audit_service.record(db, "LOGIN_SUCCESS", "auth.login", "SUCCESS", actor_id=user.user_code, actor_role=user.role, authorization_decision="ALLOWED")
    response.set_cookie(config.SESSION_COOKIE_NAME, token, httponly=True, samesite="lax", secure=config.SESSION_COOKIE_SECURE,
                        max_age=config.SESSION_ABSOLUTE_MINUTES * 60, path="/")
    return {"user": user_view(user, sess.csrf_token)}


@router.post("/logout")
def logout(response: Response, db: Session = Depends(get_db), auth: Auth = Depends(authenticated)):
    sessions.revoke_session(db, auth.session)
    audit_service.record(db, "LOGOUT", "auth.logout", "SUCCESS", actor=auth.principal)
    response.delete_cookie(config.SESSION_COOKIE_NAME, path="/")
    return {"status": "signed out"}


@router.get("/me")
def me(auth: Auth = Depends(authenticated)):
    return {"user": user_view(auth.user, auth.session.csrf_token)}


@router.get("/demo-accounts")
def demo_accounts():
    """Development convenience only; disabled (404) when SHOW_DEMO_ACCOUNTS is false."""
    if not config.SHOW_DEMO_ACCOUNTS:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Not found."})
    from app.services.seed_service import DEMO_PASSWORDS
    names = {"S2025AI001": "Aarav Mehta (AI & DS)", "S2025AI002": "Ishita Rao (AI & DS)", "S2025AI003": "Rohan Desai (AI & DS)", "S2025BD001": "Priya Nair (BDA)",
             "S2025BD002": "Kabir Shah (BDA)", "S2025BD003": "Neha Iyer (BDA)", "program.office": "Program Office (staff)", "accounts": "Accounts (staff)", "admin.campus": "Administrator"}
    return {"warning": "Fictional demo accounts for local development only.", "accounts": [{"username": u, "password": p, "label": names[u]} for u, p in DEMO_PASSWORDS.items()]}
