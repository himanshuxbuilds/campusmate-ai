from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CourseRegistration, ScholarshipRecord, StudentRecord, SupportTicket
from app.schemas import TicketCreateBody
from app.security.deps import Auth, http_error, student_only
from app.services import approval_service, audit_service, authorization_service as authz, catalog, fee_service
from app.services.clock import today
from app.tools.ticket_tools import create_ticket, ticket_dict

router = APIRouter(prefix="/api/student", tags=["student"])
# Every route here uses the SESSION identity. There is no student-ID parameter anywhere, so there is nothing to substitute.


@router.get("/profile")
def profile(db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    sid = auth.principal.user_code
    rec = db.execute(select(StudentRecord).where(StudentRecord.student_id == sid)).scalar_one_or_none()
    if rec is None:
        raise http_error(404, "PROFILE_UNAVAILABLE", "No student record is available for your account.")
    courses = db.execute(select(CourseRegistration).where(CourseRegistration.student_id == sid, CourseRegistration.status == "REGISTERED")).scalars().all()
    audit_service.record(db, "RECORD_ACCESS", "student.profile.read", "SUCCESS", actor=auth.principal, target_type="student_record", target_id=sid, authorization_decision="ALLOWED")
    masked = sid[:5] + "*" * max(0, len(sid) - 8) + sid[-3:]
    return {"student_id_masked": masked, "name": auth.user.name, "email": auth.user.email, "programme": rec.programme, "programme_name": rec.programme_name, "batch": rec.batch,
            "current_trimester": rec.current_trimester, "year1_cgpa": rec.year1_cgpa, "attendance_pct": rec.attendance_pct, "hosteller": rec.hosteller,
            "mobile": rec.mobile, "address": rec.address, "refund_bank_account_masked": rec.refund_bank_masked,
            "registered_courses": [{"course": c.course_name, "kind": c.kind, "credits": c.credits, "trimester": c.trimester} for c in courses]}


@router.get("/fees")
def fees(db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    data = fee_service.fee_status(db, auth.principal.user_code, today())
    if data is None:
        raise http_error(404, "FEE_RECORD_UNAVAILABLE", "No fee record is available for your account.")
    audit_service.record(db, "RECORD_ACCESS", "fees.read", "SUCCESS", actor=auth.principal, target_type="fee_record", target_id=auth.principal.user_code, authorization_decision="ALLOWED")
    return data


@router.get("/scholarships")
def scholarships(db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    r = db.execute(select(ScholarshipRecord).where(ScholarshipRecord.student_id == auth.principal.user_code)).scalar_one_or_none()
    if r is None:
        raise http_error(404, "SCHOLARSHIP_RECORD_UNAVAILABLE", "No scholarship record is available for your account.")
    audit_service.record(db, "RECORD_ACCESS", "scholarship.read", "SUCCESS", actor=auth.principal, target_type="scholarship_record", target_id=auth.principal.user_code, authorization_decision="ALLOWED")
    return {"scholarship_name": r.scholarship_name, "status": r.application_status, "academic_year": r.academic_year, "amount_total": r.amount_total,
            "amount_adjusted": r.amount_adjusted, "amount_remaining": r.amount_remaining, "notes": r.notes}


@router.get("/tickets")
def tickets(db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    rows = db.execute(select(SupportTicket).where(SupportTicket.student_id == auth.principal.user_code).order_by(SupportTicket.id.desc())).scalars().all()
    return {"tickets": [ticket_dict(t) for t in rows]}


@router.post("/tickets", status_code=201)
def create(body: TicketCreateBody, db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    t, dup = create_ticket(db, auth.principal, body.category, body.subject, body.description, body.priority, body.programme, body.related_reference, None)
    return {"ticket": ticket_dict(t), "duplicate_of_recent_ticket": dup, "sla": catalog.SLA[t.priority]}


@router.get("/tickets/{ticket_number}")
def ticket(ticket_number: str, db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    t = db.execute(select(SupportTicket).where(SupportTicket.ticket_number == ticket_number)).scalar_one_or_none()
    if t is None or not authz.can_view_ticket(auth.principal, t):
        raise http_error(404, "TICKET_NOT_FOUND", "No ticket with that number was found for your account.")      # same for missing and not-yours
    return {"ticket": ticket_dict(t)}


@router.get("/approvals")
def my_approvals(db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    return {"approvals": [approval_service.to_dict(r) for r in approval_service.list_requests(db, auth.principal)]}
