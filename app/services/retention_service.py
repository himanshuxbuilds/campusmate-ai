"""Retention policy (documented in docs/retention_policy.md). Run by scripts/purge_retention.py or POST /api/admin/purge-retention."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import config
from app.db import utcnow
from app.models import AgentTrace, AuditEvent, AuthSession, Conversation, ConversationMessage, LoginAttempt, Memory


def purge(db: Session) -> dict[str, Any]:
    now = utcnow()
    out: dict[str, Any] = {}
    cut = now - timedelta(days=config.RETENTION_MESSAGES_DAYS)
    out["messages"] = db.execute(delete(ConversationMessage).where(ConversationMessage.created_at < cut)).rowcount
    empty = db.execute(select(Conversation.id).where(~Conversation.id.in_(select(ConversationMessage.conversation_id).distinct()), Conversation.updated_at < cut)).scalars().all()
    for cid in empty:
        db.execute(delete(Conversation).where(Conversation.id == cid))
    out["empty_conversations"] = len(empty)
    out["traces"] = db.execute(delete(AgentTrace).where(AgentTrace.created_at < now - timedelta(days=config.RETENTION_TRACES_DAYS))).rowcount
    out["audit_events"] = db.execute(delete(AuditEvent).where(AuditEvent.created_at < now - timedelta(days=config.RETENTION_AUDIT_DAYS))).rowcount
    out["login_attempts"] = db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < now - timedelta(days=config.RETENTION_LOGIN_ATTEMPTS_DAYS))).rowcount
    out["old_sessions"] = db.execute(delete(AuthSession).where((AuthSession.expires_at < now - timedelta(days=7)) | (AuthSession.revoked_at < now - timedelta(days=7)))).rowcount
    out["expired_memories"] = db.execute(delete(Memory).where(Memory.expires_at.is_not(None), Memory.expires_at < now)).rowcount
    db.flush()
    return out
