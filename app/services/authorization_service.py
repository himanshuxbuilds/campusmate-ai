"""Centralised authorization. Every private-data access and every state change is decided HERE, in code,
before any tool or route touches the data. The language model has no say.

These functions raise `Forbidden`; they do not write audit rows themselves (the caller may be inside a
savepoint that rolls back). Callers record the denial with `record_denied`.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import ApprovalRequest, SupportTicket
from app.security.context import Principal
from app.services import audit_service

FORBIDDEN_MESSAGE = "You are not authorized to access this resource."


class Forbidden(Exception):
    code = "FORBIDDEN"

    def __init__(self, action: str, target_type: str | None = None, target_id: str | None = None, reason: str = "not authorized") -> None:
        super().__init__(FORBIDDEN_MESSAGE)
        self.action, self.target_type, self.target_id, self.reason = action, target_type, target_id, reason


def record_denied(db: Session, actor: Principal | None, exc: Forbidden, trace_id: str | None = None, commit: bool = True) -> None:
    audit_service.record(db, "ACCESS_DENIED", exc.action, "DENIED", actor=actor, target_type=exc.target_type, target_id=exc.target_id,
                         authorization_decision="DENIED", reason=exc.reason, trace_id=trace_id, commit=commit)


def resolve_student_target(actor: Principal, requested: str | None, action: str) -> str:
    """The student ID an operation may act on. Empty / the caller's own ID or exact name -> the caller. Anything else -> Forbidden.
    The same Forbidden is raised whether or not the requested person exists, so existence is never revealed."""
    if actor.role != "student":
        raise Forbidden(action, "student_record", requested, "staff/admin accounts cannot use student tools")
    req = (requested or "").strip()
    if not req or req.lower() in (actor.user_code.lower(), actor.name.lower()):
        return actor.user_code
    raise Forbidden(action, "student_record", req, "cross-student access attempt")


def require_roles(actor: Principal, roles: tuple[str, ...], action: str) -> None:
    if actor.role not in roles:
        raise Forbidden(action, None, None, f"role '{actor.role}' not permitted")


def can_view_ticket(actor: Principal, ticket: SupportTicket) -> bool:
    if actor.role == "student":
        return ticket.student_id == actor.user_code
    if actor.role == "admin":
        return True
    scope = (actor.staff_scope or {}).get("ticket_categories", [])
    return ticket.category in scope or ticket.assigned_staff_id == actor.user_code


def can_decide_approval(actor: Principal, req: ApprovalRequest) -> tuple[bool, str]:
    if actor.role not in ("staff", "admin"):
        return False, "only staff or admin may decide approvals"
    if req.requester_id == actor.user_code:
        return False, "requester cannot decide their own request"
    if actor.role == "admin":
        return True, "admin"
    if req.action_type not in (actor.staff_scope or {}).get("approval_actions", []):
        return False, "action type outside this approver's scope"
    return True, "staff in scope"


def can_view_approval(actor: Principal, req: ApprovalRequest) -> bool:
    if actor.role == "student":
        return req.requester_id == actor.user_code
    if actor.role == "admin":
        return True
    return req.action_type in (actor.staff_scope or {}).get("approval_actions", [])
