"""Profile, curriculum, calendar and late-fee tools."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import select

from app import config
from app.models import CalendarEvent, CourseRegistration, StudentRecord, User
from app.schemas import CalendarArgs, CurriculumArgs, LateFeeArgs, NoArgs
from app.services import audit_service, authorization_service as authz, fee_service
from app.tools.registry import ToolContext, ToolError, register

_ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI"}


@register("get_student_profile", "Profile of the signed-in student: programme, batch, trimester, contact details, CGPA, attendance %, masked refund account, registered courses. "
          "Takes no identity argument: the student is always the authenticated user (a different student is refused).", NoArgs)
def get_student_profile(ctx: ToolContext, a: NoArgs) -> dict[str, Any]:
    sid = authz.resolve_student_target(ctx.actor, a.student_id, "student.profile")
    rec = ctx.db.execute(select(StudentRecord).where(StudentRecord.student_id == sid)).scalar_one_or_none()
    user = ctx.db.execute(select(User).where(User.user_code == sid)).scalar_one()
    if rec is None:
        raise ToolError("PROFILE_UNAVAILABLE", "No student record is available for your account. Please contact the Program Office.")
    courses = ctx.db.execute(select(CourseRegistration).where(CourseRegistration.student_id == sid, CourseRegistration.status == "REGISTERED")).scalars().all()
    audit_service.record(ctx.db, "RECORD_ACCESS", "student.profile.read", "SUCCESS", actor=ctx.actor, target_type="student_record", target_id=sid,
                         authorization_decision="ALLOWED", trace_id=ctx.trace.trace_id)
    return {"student_id": sid, "name": user.name, "programme": rec.programme, "programme_name": rec.programme_name, "batch": rec.batch,
            "current_trimester": rec.current_trimester, "email": user.email, "mobile": rec.mobile, "address": rec.address, "hosteller": rec.hosteller,
            "year1_cgpa": rec.year1_cgpa, "attendance_current_trimester_pct": rec.attendance_pct, "refund_bank_account_masked": rec.refund_bank_masked,
            "registered_courses": [{"course": c.course_name, "kind": c.kind, "credits": c.credits, "trimester": c.trimester} for c in courses]}


@register("get_curriculum", "Trimester-wise curriculum: courses with credits, start/end and exam dates for each trimester (I-VI) of a programme (default: the student's own). "
          "Use for 'which subjects are taught in which trimester/semester', 'when does trimester N end', or the full programme plan.", CurriculumArgs)
def get_curriculum(ctx: ToolContext, a: CurriculumArgs) -> dict[str, Any]:
    plans = json.loads(config.CURRICULUM_FILE.read_text(encoding="utf-8"))["programmes"]
    prog = a.programme or ctx.actor.programme or "PGDM-AIDS"
    plan = plans.get(prog)
    if plan is None:
        raise ToolError("PROGRAMME_UNKNOWN", "Unknown programme.")
    trims = plan["trimesters"]
    if a.trimester:
        t = _ROMAN.get(a.trimester, a.trimester)
        trims = [x for x in trims if x["trimester"] == t]
    return {"programme": prog, "programme_name": plan["name"], "as_of": ctx.today.isoformat(),
            "trimesters": [{**x, "courses": [{"name": c[0], "credits": c[1]} for c in x["courses"]]} for x in trims],
            "note": "Year 1 is official (Student Handbook). Year 2 courses and Trimester V/VI dates are tentative helpdesk plans to be confirmed with the Program Office."}


@register("get_current_academic_calendar", "Upcoming academic calendar events (exams, holidays, fee due dates, trimester dates) from the trusted calendar table. "
          "Events marked TENTATIVE are not official. If nothing matches, the date is not configured.", CalendarArgs)
def get_calendar(ctx: ToolContext, a: CalendarArgs) -> dict[str, Any]:
    start = date.fromisoformat(a.from_date) if a.from_date else ctx.today
    q = (a.query or "").lower()
    kind = next((k for k in ("exam", "holiday", "fee", "academic", "scholarship") if k in q), None)
    stmt = select(CalendarEvent).where(CalendarEvent.end_date >= start)
    if a.to_date:
        stmt = stmt.where(CalendarEvent.start_date <= date.fromisoformat(a.to_date))
    if kind:
        stmt = stmt.where(CalendarEvent.event_type == kind)
    rows = ctx.db.execute(stmt.order_by(CalendarEvent.start_date)).scalars().all()
    if q and not kind:
        words = [w for w in q.split() if len(w) > 3]
        narrowed = [r for r in rows if any(w in r.title.lower() for w in words)]
        rows = narrowed or rows
    rows = rows[: a.limit]
    return {"as_of": ctx.today.isoformat(), "events": [{"title": r.title, "start": r.start_date.isoformat(), "end": r.end_date.isoformat(), "type": r.event_type,
                                                          "status": r.status, "kb": r.kb_ref} for r in rows],
            "note": ("No verified date is configured for that." if not rows else "TENTATIVE events come from the helpdesk calendar and must be confirmed with the Program Office.")}


@register("compute_late_fee", "Calculate the tuition late fee under the Late Fees Policy for a number of days late, or for a due date and optional payment date.", LateFeeArgs)
def compute_late_fee(ctx: ToolContext, a: LateFeeArgs) -> dict[str, Any]:
    days = a.days_late
    if days is None:
        pay = date.fromisoformat(a.payment_date) if a.payment_date else ctx.today
        days = (pay - date.fromisoformat(a.due_date)).days
        if days < 0:
            raise ToolError("NOT_LATE", "That payment date is before the due date, so no late fee applies.")
    out = fee_service.late_fee_for(days, a.fee_amount, permitted_beyond_6_weeks=days > 42)
    out["interpretation"] = "progressive bands: days 1-14 at INR 100, 15-28 at INR 200, 29-42 at INR 400 [F.3.2]"
    return out
