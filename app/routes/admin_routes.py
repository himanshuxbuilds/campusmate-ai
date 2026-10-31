from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db, iso, utcnow
from app.models import ApprovalRequest, AuditEvent, SupportTicket, User
from app.schemas import TicketUpdateBody, UserUpdateBody
from app.security import sessions
from app.security.deps import Auth, admin_only, http_error, staff_or_admin
from app.services import audit_service, authorization_service as authz, catalog, retention_service, trace_service
from app.tools.ticket_tools import ticket_dict

router = APIRouter(prefix="/api/admin", tags=["admin"])
STAFF_AUDIT_TYPES = {"ACCESS_DENIED", "TOOL_FAILURE", "TOOL_REJECTED", "LLM_FAILURE", "SENSITIVE_REQUEST", "APPROVAL_DECISION", "RECORD_MODIFIED", "TICKET_CREATED", "TICKET_UPDATED", "RATE_LIMITED"}


@router.get("/audit-events")
def audit_events(event_type: str | None = None, outcome: str | None = None, actor_id: str | None = None, hours: int | None = None, limit: int = 50, offset: int = 0,
                 db: Session = Depends(get_db), auth: Auth = Depends(staff_or_admin)):
    if event_type and event_type not in audit_service.EVENT_TYPES:
        raise http_error(422, "INVALID_EVENT_TYPE", "Unknown event type.")
    if auth.principal.role == "staff":                           # staff see operational events, not other people's logins
        if event_type and event_type not in STAFF_AUDIT_TYPES:
            raise authz.Forbidden("audit.read", "audit_event", event_type, "staff may not read this event type")
    since = utcnow() - timedelta(hours=hours) if hours else None
    rows, total = audit_service.query(db, event_type=event_type, outcome=outcome, actor_id=actor_id, since=since, limit=limit, offset=offset)
    if auth.principal.role == "staff":
        rows = [r for r in rows if r.event_type in STAFF_AUDIT_TYPES]
    return {"total": total, "limit": limit, "offset": offset, "events": [audit_service.to_dict(r) for r in rows]}


@router.get("/traces")
def traces(outcome: str | None = None, user_id: str | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db), auth: Auth = Depends(staff_or_admin)):
    return {"traces": [trace_service.to_dict(t) for t in trace_service.query(db, user_id=user_id, outcome=outcome, limit=limit, offset=offset)]}


@router.get("/metrics")
def metrics(db: Session = Depends(get_db), auth: Auth = Depends(staff_or_admin)):
    day = utcnow() - timedelta(hours=24)
    def n(stmt) -> int: return db.execute(stmt).scalar_one()
    return {"pending_approvals": n(select(func.count()).select_from(ApprovalRequest).where(ApprovalRequest.status == "PENDING")),
            "open_tickets": n(select(func.count()).select_from(SupportTicket).where(SupportTicket.status.in_(("OPEN", "IN_PROGRESS", "AWAITING_STUDENT", "ESCALATED")))),
            "access_denied_24h": n(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "ACCESS_DENIED", AuditEvent.created_at >= day)),
            "tool_failures_24h": n(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type.in_(("TOOL_FAILURE", "TOOL_REJECTED")), AuditEvent.created_at >= day)),
            "llm_failures_24h": n(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "LLM_FAILURE", AuditEvent.created_at >= day)),
            "failed_logins_24h": n(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "LOGIN_FAILURE", AuditEvent.created_at >= day))}


@router.get("/evaluation")
def evaluation(auth: Auth = Depends(staff_or_admin)):
    """Latest saved evaluation run (produced by scripts/run_evaluation.py). Nothing here is computed on the fly or invented."""
    f = config.EVAL_RESULTS_DIR / "latest.json"
    if not f.exists():
        return {"available": False, "message": "No evaluation has been run yet. Run: python scripts/run_evaluation.py"}
    return {"available": True, **json.loads(f.read_text(encoding="utf-8"))}


@router.get("/tickets")
def tickets(status: str | None = None, category: str | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db), auth: Auth = Depends(staff_or_admin)):
    stmt = select(SupportTicket)
    if auth.principal.role == "staff":
        scope = (auth.principal.staff_scope or {}).get("ticket_categories", []) or ["__none__"]
        stmt = stmt.where((SupportTicket.category.in_(scope)) | (SupportTicket.assigned_staff_id == auth.principal.user_code))
    if status: stmt = stmt.where(SupportTicket.status == status)
    if category: stmt = stmt.where(SupportTicket.category == category)
    rows = db.execute(stmt.order_by(SupportTicket.id.desc()).limit(max(1, min(limit, 200))).offset(max(0, offset))).scalars().all()
    return {"tickets": [{**ticket_dict(t), "student_id": t.student_id, "assigned_staff_id": t.assigned_staff_id} for t in rows]}


@router.patch("/tickets/{ticket_number}")
def update_ticket(ticket_number: str, body: TicketUpdateBody, db: Session = Depends(get_db), auth: Auth = Depends(staff_or_admin)):
    t = db.execute(select(SupportTicket).where(SupportTicket.ticket_number == ticket_number)).scalar_one_or_none()
    if t is None or not authz.can_view_ticket(auth.principal, t):
        raise http_error(404, "TICKET_NOT_FOUND", "Ticket not found.")
    changed = []
    if body.status: t.status = body.status; changed.append("status")
    if body.staff_note is not None: t.staff_note = body.staff_note; changed.append("staff_note")
    if body.assign_to_me: t.assigned_staff_id = auth.principal.user_code; changed.append("assigned_staff_id")
    audit_service.record(db, "TICKET_UPDATED", "ticket.update", "SUCCESS", actor=auth.principal, target_type="support_ticket", target_id=t.ticket_number,
                         authorization_decision="ALLOWED", details={"fields": changed})
    return {"ticket": {**ticket_dict(t), "student_id": t.student_id, "assigned_staff_id": t.assigned_staff_id}}


@router.get("/users")
def users(db: Session = Depends(get_db), auth: Auth = Depends(admin_only)):
    rows = db.execute(select(User).order_by(User.role, User.user_code)).scalars().all()
    return {"users": [{"user_code": u.user_code, "name": u.name, "role": u.role, "programme": u.programme, "is_active": u.is_active, "staff_scope": u.staff_scope} for u in rows]}


@router.patch("/users/{user_code}")
def update_user(user_code: str, body: UserUpdateBody, db: Session = Depends(get_db), auth: Auth = Depends(admin_only)):
    u = db.execute(select(User).where(User.user_code == user_code)).scalar_one_or_none()
    if u is None:
        raise http_error(404, "USER_NOT_FOUND", "User not found.")
    if u.user_code == auth.principal.user_code:
        raise http_error(409, "SELF_CHANGE", "Administrators cannot change their own role or active status.")
    changed = []
    if body.is_active is not None and body.is_active != u.is_active:
        u.is_active = body.is_active; changed.append("is_active")
        if not body.is_active:
            sessions.revoke_all_for_user(db, u.user_code)
    if body.role and body.role != u.role:
        u.role = body.role; changed.append("role"); sessions.revoke_all_for_user(db, u.user_code)
    audit_service.record(db, "ADMIN_ACTION", "user.update", "SUCCESS", actor=auth.principal, target_type="user", target_id=u.user_code, authorization_decision="ALLOWED", details={"fields": changed})
    return {"user": {"user_code": u.user_code, "role": u.role, "is_active": u.is_active}}


@router.post("/purge-retention")
def purge(db: Session = Depends(get_db), auth: Auth = Depends(admin_only)):
    out = retention_service.purge(db)
    audit_service.record(db, "ADMIN_ACTION", "retention.purge", "SUCCESS", actor=auth.principal, authorization_decision="ALLOWED", details=out)
    return {"purged": out}
