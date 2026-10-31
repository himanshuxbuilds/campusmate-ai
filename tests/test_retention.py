from datetime import timedelta

from sqlalchemy import func, select

from app import config, db
from app.db import utcnow
from app.models import AgentTrace, AuditEvent, AuthSession, Conversation, ConversationMessage, LoginAttempt, Memory


def count(model):
    with db.session_scope() as s:
        return s.execute(select(func.count()).select_from(model)).scalar_one()


def test_purge_removes_only_data_older_than_the_policy(aarav, admin):
    aarav.chat("hello"); aarav.chat("Please call me Aarav"); aarav.get("/api/student/fees")
    fresh = {m: count(m) for m in (ConversationMessage, AgentTrace, AuditEvent, LoginAttempt)}
    old = utcnow() - timedelta(days=max(config.RETENTION_MESSAGES_DAYS, config.RETENTION_TRACES_DAYS, config.RETENTION_AUDIT_DAYS, config.RETENTION_LOGIN_ATTEMPTS_DAYS) + 5)
    with db.session_scope() as s:
        conv = Conversation(id="a" * 32, user_id="S2025AI001", title="old", created_at=old, updated_at=old); s.add(conv); s.flush()
        s.add(ConversationMessage(conversation_id=conv.id, role="user", message_content="old message", created_at=old))
        s.add(AgentTrace(trace_id="TR-OLD", user_id="S2025AI001", provider="mock", model_name="m", outcome="answered", created_at=old))
        s.add(AuditEvent(event_id="AUD-OLD", event_type="LOGOUT", action="x", outcome="SUCCESS", created_at=old))
        s.add(LoginAttempt(username="x", ip="1.1.1.1", success=False, created_at=old))
        s.add(AuthSession(token_hash="h" * 64, user_id="S2025AI001", csrf_token="c", created_at=old, last_seen_at=old, expires_at=old))
        s.add(Memory(user_id="S2025AI001", category="plan", memory_key="plan", memory_value="old plan", expires_at=utcnow() - timedelta(days=1)))
    r = admin.post("/api/admin/purge-retention").json()["purged"]
    assert r["messages"] == 1 and r["traces"] == 1 and r["audit_events"] == 1 and r["login_attempts"] == 1 and r["old_sessions"] == 1 and r["expired_memories"] == 1 and r["empty_conversations"] == 1
    assert count(AgentTrace) == fresh[AgentTrace] and count(ConversationMessage) == fresh[ConversationMessage] and count(LoginAttempt) == fresh[LoginAttempt]
    assert count(AuditEvent) >= fresh[AuditEvent]                                    # recent audit rows survive (plus the purge's own ADMIN_ACTION record)
    assert aarav.get("/api/auth/me").status_code == 200                              # the live session was not touched


def test_purge_is_audited(admin):
    admin.post("/api/admin/purge-retention")
    with db.session_scope() as s:
        assert s.execute(select(AuditEvent).where(AuditEvent.action == "retention.purge")).scalar_one().outcome == "SUCCESS"
