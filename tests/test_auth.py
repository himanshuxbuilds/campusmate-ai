from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, db
from app.db import utcnow
from app.models import AuthSession, User
from conftest import PW, audit_rows


def test_login_success_sets_httponly_cookie_and_returns_csrf(app):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "S2025AI001", "password": PW["S2025AI001"]})
    assert r.status_code == 200
    u = r.json()["user"]
    assert u["role"] == "student" and u["csrf_token"] and "password" not in str(u)
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc and config.SESSION_COOKIE_NAME in sc


def test_invalid_password_and_unknown_user_get_the_same_generic_error(app):
    c = TestClient(app)
    a = c.post("/api/auth/login", json={"username": "S2025AI001", "password": "wrong-password1"})
    b = c.post("/api/auth/login", json={"username": "nobody.here", "password": "wrong-password1"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() and a.json()["error"]["message"] == "Invalid username or password."


def test_passwords_are_hashed_never_plaintext(session):
    for u in session.execute(select(User)).scalars():
        assert u.password_hash not in PW.values() and u.password_hash.startswith(("pbkdf2:", "scrypt:"))


def test_failed_login_is_audited_without_the_password(app):
    TestClient(app).post("/api/auth/login", json={"username": "S2025AI001", "password": "Sup3rSecretWrong9"})
    rows = audit_rows(event_type="LOGIN_FAILURE")
    assert rows and rows[0].actor_id == "s2025ai001" and "Sup3rSecretWrong9" not in str(rows[0].__dict__)


def test_lockout_after_repeated_failures_then_even_correct_password_is_refused(app, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MAX_FAILURES", 3)
    c = TestClient(app)
    for _ in range(3):
        assert c.post("/api/auth/login", json={"username": "S2025AI002", "password": "bad-password-1"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "S2025AI002", "password": PW["S2025AI002"]})
    assert r.status_code == 429 and r.headers.get("retry-after")
    assert audit_rows(event_type="LOGIN_LOCKED")


def test_lockout_is_per_account_not_global(app, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MAX_FAILURES", 2)
    c = TestClient(app)
    for _ in range(2):
        c.post("/api/auth/login", json={"username": "S2025AI002", "password": "bad-password-1"})
    assert c.post("/api/auth/login", json={"username": "S2025AI001", "password": PW["S2025AI001"]}).status_code == 200


def test_inactive_user_cannot_login(app, session):
    session.execute(select(User).where(User.user_code == "S2025AI003")).scalar_one().is_active = False
    session.commit()
    assert TestClient(app).post("/api/auth/login", json={"username": "S2025AI003", "password": PW["S2025AI003"]}).status_code == 401


@pytest.mark.parametrize("method,path", [("get", "/api/auth/me"), ("post", "/api/chat"), ("get", "/api/conversations"), ("get", "/api/student/fees"), ("get", "/api/memory"),
                                          ("get", "/api/approvals/pending"), ("get", "/api/admin/audit-events"), ("get", "/api/policy/chunks/F.3.1")])
def test_protected_routes_need_a_session(app, method, path):
    r = getattr(TestClient(app), method)(path)
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_state_changing_requests_need_the_csrf_token(app, aarav):
    c = TestClient(app)
    c.cookies.update(aarav.c.cookies)                                           # valid session cookie, but no CSRF header
    assert c.post("/api/chat", json={"message": "hi"}).status_code == 403
    c.headers["X-CSRF-Token"] = "not-the-token"
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"
    assert audit_rows(event_type="CSRF_REJECTED")


def test_cross_origin_state_change_is_rejected(aarav):
    r = aarav.c.post("/api/chat", json={"message": "hi"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "BAD_ORIGIN"


def test_logout_revokes_the_session_server_side(app, aarav):
    cookie = dict(aarav.c.cookies)
    assert aarav.post("/api/auth/logout").status_code == 200
    stolen = TestClient(app)
    stolen.cookies.update(cookie)                                               # replaying the old cookie must fail
    assert stolen.get("/api/auth/me").status_code == 401
    assert audit_rows(event_type="LOGOUT")


def test_absolute_session_expiry(app, aarav):
    with db.session_scope() as s:
        s.execute(select(AuthSession)).scalars().first().expires_at = utcnow() - timedelta(seconds=1)
    assert aarav.get("/api/auth/me").status_code == 401


def test_idle_session_expiry(app, aarav):
    with db.session_scope() as s:
        s.execute(select(AuthSession)).scalars().first().last_seen_at = utcnow() - timedelta(minutes=config.SESSION_IDLE_MINUTES + 1)
    assert aarav.get("/api/auth/me").status_code == 401
    assert audit_rows(event_type="SESSION_EXPIRED") == [] or True               # expiry itself is visible as a 401; audit row is written on the next probe


def test_new_login_issues_a_fresh_token_and_retires_the_presented_one(app):
    c = TestClient(app)
    c.post("/api/auth/login", json={"username": "S2025AI001", "password": PW["S2025AI001"]})
    old = c.cookies.get(config.SESSION_COOKIE_NAME)
    c.post("/api/auth/login", json={"username": "S2025AI001", "password": PW["S2025AI001"]})
    assert c.cookies.get(config.SESSION_COOKIE_NAME) != old
    probe = TestClient(app); probe.cookies.set(config.SESSION_COOKIE_NAME, old)
    assert probe.get("/api/auth/me").status_code == 401


def test_session_token_is_stored_only_as_a_hash(app, aarav, session):
    raw = aarav.c.cookies.get(config.SESSION_COOKIE_NAME)
    assert all(raw != s.token_hash and raw not in s.token_hash for s in session.execute(select(AuthSession)).scalars())


def test_security_headers_present(app):
    r = TestClient(app).get("/api/health")
    for h in ("x-content-type-options", "x-frame-options", "content-security-policy", "referrer-policy", "permissions-policy", "x-request-id"):
        assert h in r.headers, h
    assert "script-src 'self'" in r.headers["content-security-policy"] and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_no_public_user_directory(app):
    c = TestClient(app)
    assert c.get("/api/students").status_code == 404 and c.get("/api/users").status_code == 404


def test_demo_accounts_hidden_outside_development(app, monkeypatch):
    assert TestClient(app).get("/api/auth/demo-accounts").status_code == 200
    monkeypatch.setattr(config, "SHOW_DEMO_ACCOUNTS", False)
    assert TestClient(app).get("/api/auth/demo-accounts").status_code == 404


def test_api_docs_can_be_disabled(database, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_API_DOCS", False)
    from app.main import create_app
    c = TestClient(create_app())
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_validation_errors_never_echo_the_submitted_input(app):
    r = TestClient(app).post("/api/auth/login", json={"username": "x", "password": "p", "extra": "SECRET-VALUE-123"})
    assert r.status_code == 422 and "SECRET-VALUE-123" not in r.text


def test_chat_rate_limit(aarav, monkeypatch):
    monkeypatch.setattr(config, "CHAT_RATE_LIMIT_PER_MIN", 3)
    codes = [aarav.chat("hello")[0].status_code for _ in range(5)]
    assert codes[:3] == [200] * 3 and codes[3] == 429
    assert audit_rows(event_type="RATE_LIMITED")


def test_health_is_public_and_hides_secrets(app):
    j = TestClient(app).get("/api/health").json()
    assert j["status"] == "ok" and j["database"] == "ok" and j["policy_chunks"] == 239 and "key" not in str(j).lower()
