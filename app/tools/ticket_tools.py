from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import config
from app.db import iso, utcnow
from app.models import SupportTicket
from app.schemas import CreateTicketArgs, TicketStatusArgs
from app.services import audit_service, authorization_service as authz, catalog
from app.tools.registry import ToolContext, ToolError, register


def ticket_dict(t: SupportTicket, include_note: bool = True) -> dict[str, Any]:
    d = {"ticket_number": t.ticket_number, "category": t.category, "category_label": catalog.TICKET_CATEGORY_LABELS.get(t.category, t.category),
         "subject": t.subject, "priority": t.priority, "status": t.status, "created_at": iso(t.created_at), "updated_at": iso(t.updated_at)}
    if include_note:
        d["staff_note"] = t.staff_note
    return d


def next_ticket_number(db) -> str:
    nums = [int(m.group(1)) for (n,) in db.execute(select(SupportTicket.ticket_number)).all() if (m := re.search(r"-(\d{4})$", n))]
    return f"SR-{utcnow().year}-{(max(nums) + 1) if nums else 1:04d}"


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def create_ticket(db, actor, category: str, subject: str, description: str, priority: str, programme: str | None, related_reference: str | None,
                  trace_id: str | None) -> tuple[SupportTicket, bool]:
    """Create a ticket for `actor`. Returns (ticket, duplicate). A repeat of the same open ticket within the window returns the existing one."""
    since = utcnow() - timedelta(minutes=config.TICKET_DUPLICATE_WINDOW_MINUTES)
    recent = db.execute(select(SupportTicket).where(SupportTicket.student_id == actor.user_code, SupportTicket.category == category,
                                                    SupportTicket.created_at >= since, SupportTicket.status.in_(("OPEN", "IN_PROGRESS", "AWAITING_STUDENT")))).scalars().all()
    for t in recent:
        if _norm(t.subject) == _norm(subject):
            return t, True
    for attempt in range(3):
        t = SupportTicket(ticket_number=next_ticket_number(db), student_id=actor.user_code, category=category, subject=subject, description=description,
                          priority=priority, status="OPEN", programme=programme, related_reference=related_reference)
        sp = db.begin_nested()
        try:
            db.add(t); db.flush(); sp.commit()
            break
        except IntegrityError:
            sp.rollback()                                       # ticket number raced; recompute
    else:
        raise ToolError("TICKET_FAILED", "The ticket could not be created. Please try again.")
    audit_service.record(db, "TICKET_CREATED", "ticket.create", "SUCCESS", actor=actor, target_type="support_ticket", target_id=t.ticket_number,
                         authorization_decision="ALLOWED", reason=f"{category}/{priority}", trace_id=trace_id)
    return t, False


@register("create_support_ticket", "Create a helpdesk ticket for the signed-in student. Use when the student asks to raise/log a request or report a problem. "
          "Always created for the authenticated student; repeating the same open ticket returns the existing one.", CreateTicketArgs)
def create_support_ticket(ctx: ToolContext, a: CreateTicketArgs) -> dict[str, Any]:
    authz.resolve_student_target(ctx.actor, a.student_id, "ticket.create")
    t, dup = create_ticket(ctx.db, ctx.actor, a.category, a.subject, a.description, a.priority, a.programme, a.related_reference, ctx.trace.trace_id)
    return {"ticket": ticket_dict(t), "duplicate_of_recent_ticket": dup, "sla": catalog.SLA[t.priority], "policy_refs": ["M.1.1", "M.1.2"]}


@register("get_ticket_status", "Status of one support ticket by its number (e.g. SR-2026-0101). Only the student's own tickets are visible; any other number gets the same "
          "'not found' answer.", TicketStatusArgs)
def get_ticket_status(ctx: ToolContext, a: TicketStatusArgs) -> dict[str, Any]:
    t = ctx.db.execute(select(SupportTicket).where(SupportTicket.ticket_number == a.ticket_number)).scalar_one_or_none()
    if t is None or not authz.can_view_ticket(ctx.actor, t):
        if t is not None:                                       # exists but is not theirs: log it, answer identically
            audit_service.record(ctx.db, "ACCESS_DENIED", "ticket.read", "DENIED", actor=ctx.actor, target_type="support_ticket", target_id=a.ticket_number,
                                 authorization_decision="DENIED", reason="not the ticket owner", trace_id=ctx.trace.trace_id)
        raise ToolError("TICKET_NOT_FOUND", "No ticket with that number was found for your account.")
    return {"ticket": ticket_dict(t)}
