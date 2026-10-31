"""Security and business audit trail (separate from agent traces).

Rules enforced here: no passwords, tokens, OTPs or API keys; PII is masked; every record carries
actor, role, action, target, authorization decision, approval status, outcome, reason and the
trace/correlation IDs. Records are append-only (no update/delete API exists).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import iso
from app.models import AuditEvent
from app.security.context import Principal, correlation_id, new_id
from app.security.redaction import redact_args

EVENT_TYPES = {
    "LOGIN_SUCCESS", "LOGIN_FAILURE", "LOGIN_LOCKED", "LOGOUT", "SESSION_EXPIRED", "ACCESS_DENIED", "RECORD_ACCESS", "TICKET_CREATED",
    "TICKET_UPDATED", "SENSITIVE_REQUEST", "APPROVAL_DECISION", "RECORD_MODIFIED", "TOOL_FAILURE", "TOOL_REJECTED", "LLM_FAILURE",
    "MEMORY_CREATED", "MEMORY_DELETED", "CONVERSATION_DELETED", "ADMIN_ACTION", "SECRET_SHARED", "RATE_LIMITED", "CSRF_REJECTED",
}


def record(db: Session, event_type: str, action: str, outcome: str, *, actor: Principal | None = None, actor_id: str | None = None,
           actor_role: str | None = None, target_type: str | None = None, target_id: str | None = None,
           authorization_decision: str | None = None, approval_status: str | None = None, approver_id: str | None = None,
           reason: str | None = None, trace_id: str | None = None, details: dict[str, Any] | None = None, commit: bool = False) -> AuditEvent:
    assert event_type in EVENT_TYPES, f"unknown audit event type {event_type}"
    ev = AuditEvent(event_id=new_id("AUD-"), event_type=event_type, actor_id=(actor.user_code if actor else actor_id),
                    actor_role=(actor.role if actor else actor_role), action=action, target_type=target_type, target_id=target_id,
                    authorization_decision=authorization_decision, approval_status=approval_status, approver_id=approver_id,
                    outcome=outcome, reason=(reason or "")[:200] or None, trace_id=trace_id, correlation_id=correlation_id(),
                    details=redact_args(details) if details else None)
    db.add(ev)
    db.flush()
    if commit:
        db.commit()
    return ev


def to_dict(e: AuditEvent) -> dict[str, Any]:
    return {"event_id": e.event_id, "timestamp": iso(e.created_at), "event_type": e.event_type, "actor_id": e.actor_id, "actor_role": e.actor_role,
            "action": e.action, "target_type": e.target_type, "target_id": e.target_id, "authorization_decision": e.authorization_decision,
            "approval_status": e.approval_status, "approver_id": e.approver_id, "outcome": e.outcome, "reason": e.reason,
            "trace_id": e.trace_id, "correlation_id": e.correlation_id, "details": e.details}


def query(db: Session, *, event_type: str | None = None, outcome: str | None = None, actor_id: str | None = None,
          since: datetime | None = None, limit: int = 50, offset: int = 0) -> tuple[list[AuditEvent], int]:
    stmt = select(AuditEvent)
    if event_type: stmt = stmt.where(AuditEvent.event_type == event_type)
    if outcome: stmt = stmt.where(AuditEvent.outcome == outcome)
    if actor_id: stmt = stmt.where(AuditEvent.actor_id == actor_id)
    if since: stmt = stmt.where(AuditEvent.created_at >= since)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(AuditEvent.id.desc()).limit(max(1, min(limit, 200))).offset(max(0, offset))).scalars().all()
    return list(rows), total
