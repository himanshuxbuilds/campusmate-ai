"""Test fixtures. Environment is fixed BEFORE the app is imported. Every test gets a pristine copy of a seeded SQLite database that
was built through the real Alembic migration (so the migration itself is exercised by the whole suite)."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Offline tests must never use a real key, so GROQ_* are blanked below. Stash the real values first (environment, then .env) for the optional LIVE tests only.
from dotenv import dotenv_values                                                  # noqa: E402
_dot = dotenv_values(ROOT / ".env")
LIVE_ENV = {k: (os.environ.get(k) or _dot.get(k) or "") for k in ("GROQ_API_KEY", "GROQ_MODEL", "GROQ_BASE_URL")}
os.environ.update(APP_ENV="test", LLM_PROVIDER="mock", PASSWORD_HASH_METHOD="pbkdf2:sha256:1000", DEMO_TODAY="2026-10-30", SECRET_KEY="test-secret-key-not-for-production-0123456789",
                  GROQ_API_KEY="", GROQ_MODEL="", SESSION_COOKIE_SECURE="false", SHOW_DEMO_ACCOUNTS="true", LOG_LEVEL="CRITICAL")

import pytest                                                                     # noqa: E402
from fastapi.testclient import TestClient                                          # noqa: E402

from app import config, db                                                         # noqa: E402
from app.security.ratelimit import limiter                                         # noqa: E402
from app.services import llm_client, retrieval_service                             # noqa: E402
from app.services.seed_service import DEMO_PASSWORDS                               # noqa: E402

PW = DEMO_PASSWORDS


@pytest.fixture(scope="session")
def template_db():
    d = Path(tempfile.mkdtemp(prefix="campus_tpl_"))
    url = f"sqlite:///{d / 'template.db'}"
    from scripts.init_db import upgrade
    upgrade(url)                                                                   # real Alembic migration
    db.init_engine(url)
    from app.services.ingest_service import ingest_policy
    from app.services.seed_service import seed_demo_data
    with db.session_scope() as s:
        seed_demo_data(s)
        ingest_policy(s)
    db.engine.dispose()
    for suffix in ("-wal", "-shm"):
        (d / f"template.db{suffix}").unlink(missing_ok=True)
    yield d / "template.db"
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def database(template_db, tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    shutil.copy(template_db, path)
    url = f"sqlite:///{path}"
    monkeypatch.setattr(config, "DATABASE_URL", url)
    db.init_engine(url)
    limiter.reset()
    retrieval_service.invalidate()
    llm_client.reset_client()
    yield url
    db.engine.dispose()
    llm_client.reset_client()


@pytest.fixture()
def app(database):
    from app.main import create_app
    return create_app()


@pytest.fixture()
def session(database):
    """A plain session: tests commit explicitly; teardown always rolls back and closes (so a deliberately failed flush cannot break teardown,
    and SQLite's write lock is always released)."""
    s = db.SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


class Api:
    """Logged-in TestClient with the CSRF header attached."""
    def __init__(self, app, user: str, password: str | None = None) -> None:
        self.c = TestClient(app)
        r = self.c.post("/api/auth/login", json={"username": user, "password": password or PW[user]})
        assert r.status_code == 200, r.text
        self.user = r.json()["user"]
        self.c.headers["X-CSRF-Token"] = self.user["csrf_token"]

    def get(self, *a, **k): return self.c.get(*a, **k)
    def post(self, *a, **k): return self.c.post(*a, **k)
    def delete(self, *a, **k): return self.c.delete(*a, **k)
    def patch(self, *a, **k): return self.c.patch(*a, **k)

    def chat(self, text: str, cid: str | None = None, **extra):
        body = {"message": text, **({"conversation_id": cid} if cid else {}), **extra}
        r = self.c.post("/api/chat", json=body)
        return r, (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})


@pytest.fixture()
def login(app):
    return lambda user, password=None: Api(app, user, password)


@pytest.fixture()
def aarav(login): return login("S2025AI001")
@pytest.fixture()
def ishita(login): return login("S2025AI002")
@pytest.fixture()
def staff(login): return login("program.office")
@pytest.fixture()
def accounts(login): return login("accounts")
@pytest.fixture()
def admin(login): return login("admin.campus")


def audit_rows(**filters):
    from sqlalchemy import select
    from app.models import AuditEvent
    with db.session_scope() as s:
        q = select(AuditEvent)
        for k, v in filters.items():
            q = q.where(getattr(AuditEvent, k) == v)
        rows = s.execute(q.order_by(AuditEvent.id)).scalars().all()
        for r in rows:
            s.expunge(r)
        return rows


def pending(api: Api, ask: str = "Change my mobile number to +91 98765 43210") -> str:
    r1, j1 = api.chat(ask)
    r2, j2 = api.chat("yes", j1["conversation_id"])
    return j2["approvals"][0]["request_number"]
