"""Demo/seed data. Fictional people only. Demo passwords exist ONLY here, are hashed on insert, and seeding
refuses to run outside development unless explicitly forced (there is no default production admin)."""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import (CalendarEvent, CourseRegistration, FeeInstallment, FeeRecord, ScholarshipRecord, StudentRecord, SupportTicket, User)
from app.security.passwords import hash_password
from app.services import catalog
from app.services.fee_service import recompute_fee_record

DEMO_PASSWORDS = {
    "S2025AI001": "Demo@1101", "S2025AI002": "Demo@1102", "S2025AI003": "Demo@1103",
    "S2025BD001": "Demo@2101", "S2025BD002": "Demo@2102", "S2025BD003": "Demo@2103",
    "program.office": "Staff@9000", "accounts": "Staff@9001", "admin.campus": "Admin@9999",
}
STAFF = [
    ("program.office", "Program Office (Mr. Jay Joshi)", "program.office@aidtm.ac.in", "staff",
     {"approval_actions": catalog.PROGRAM_OFFICE_ACTIONS, "ticket_categories": catalog.PROGRAM_OFFICE_TICKETS}),
    ("accounts", "Accounts (Mr. Ashok Rupera)", "accounts@aidtm.ac.in", "staff",
     {"approval_actions": catalog.ACCOUNTS_ACTIONS, "ticket_categories": catalog.ACCOUNTS_TICKETS}),
    ("admin.campus", "Campus Administrator", "admin@aidtm.ac.in", "admin", {}),
]
LEGACY_CATEGORY = {"Library": "LIBRARY", "Fees & Payments": "FEES", "Certificates & Documents": "DOCUMENTS"}
LEGACY_SCHOLARSHIP = {"NONE": "NOT_APPLIED", "APPLIED": "SUBMITTED", "UNDER_REVIEW": "UNDER_REVIEW", "APPROVED": "APPROVED",
                      "ADJUSTED": "APPROVED", "ON_HOLD": "ADDITIONAL_DOCUMENTS_REQUIRED", "REJECTED": "REJECTED", "WITHDRAWN": "WITHDRAWN"}


class SeedRefused(RuntimeError):
    pass


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def seed_demo_data(db: Session, force: bool = False) -> dict[str, int]:
    if not (config.IS_DEV or force):
        raise SeedRefused("Demo seeding is limited to development. Create real users with scripts/create_user.py.")
    if db.execute(select(User.id).limit(1)).first():
        return {"status": "already_seeded"}  # type: ignore[return-value]
    data = json.loads((config.DATA_DIR / "students.json").read_text(encoding="utf-8"))
    curriculum = json.loads(config.CURRICULUM_FILE.read_text(encoding="utf-8"))["programmes"]
    counts = {"users": 0, "tickets": 0, "installments": 0, "courses": 0, "calendar_events": 0}
    for code, name, email, role, scope in STAFF:
        db.add(User(user_code=code, name=name, email=email, password_hash=hash_password(DEMO_PASSWORDS[code]), role=role, staff_scope=scope or None))
        counts["users"] += 1
    db.flush()
    ticket_total = 0
    for s in data["students"]:
        sid = s["student_id"]
        db.add(User(user_code=sid, name=s["name"], email=s["email"], password_hash=hash_password(DEMO_PASSWORDS[sid]), role="student", programme=s["programme"]))
        db.flush()
        db.add(StudentRecord(student_id=sid, academic_year="2026-27", programme=s["programme"], programme_name=s["programme_name"], batch=s["batch"],
                             current_trimester=s["current_trimester"], year1_cgpa=s["year1_cgpa"], attendance_pct=s["attendance_current_trimester_pct"],
                             hosteller=s["hosteller"], address=s["address"], mobile=s["mobile"], refund_bank_masked=s["refund_bank_account_masked"]))
        f = s["fees"]
        db.add(FeeRecord(student_id=sid, academic_year="2025-27", total_tuition=f["total_tuition"], caution_deposit=f["caution_deposit"],
                         scholarship_adjustment_total=f.get("scholarship_adjustment_total", 0), total_fee=0, amount_paid=0, amount_due=0, status="UNPAID"))
        for i in f["installments"]:
            db.add(FeeInstallment(student_id=sid, name=i["name"], due_date=_d(i["due_date"]), amount=i["amount"], status=i["status"], paid_on=_d(i.get("paid_on")),
                                  late_fee=i.get("late_fee"), late_fee_paid=bool(i.get("late_fee_paid", True)), note=i.get("note")))
            counts["installments"] += 1
        db.flush()
        recompute_fee_record(db, sid)
        sc = s["scholarship"]
        db.add(ScholarshipRecord(student_id=sid, scholarship_name=sc.get("type"), application_status=LEGACY_SCHOLARSHIP[sc["status"]], academic_year="2026-27",
                                 amount_total=sc.get("amount_total"), amount_adjusted=sc.get("amount_adjusted"), amount_remaining=sc.get("amount_remaining"),
                                 notes=sc.get("notes"), applied_on=_d(sc.get("applied_on")), decided_on=_d(sc.get("decided_on"))))
        trim = next(t for t in curriculum[s["programme"]]["trimesters"] if t["trimester"] == s["current_trimester"])
        for cname, cr in trim["courses"]:
            kind = "elective" if cname in catalog.ELECTIVE_BASKET else "core"
            db.add(CourseRegistration(student_id=sid, course_name=cname, trimester=trim["trimester"], kind=kind, credits=cr)); counts["courses"] += 1
        if not any(c.kind == "elective" for c in [x for x in db.new if isinstance(x, CourseRegistration) and x.student_id == sid]):
            db.add(CourseRegistration(student_id=sid, course_name="Entrepreneurship Essentials", trimester=trim["trimester"], kind="elective", credits=3)); counts["courses"] += 1
        for t in s.get("support_requests", []):
            ticket_total += 1
            db.add(SupportTicket(ticket_number=t["id"], student_id=sid, category=LEGACY_CATEGORY.get(t["category"], "OTHER"), subject=t["subject"],
                                 description=t.get("resolution", ""), priority="NORMAL", status=t["status"], staff_note=t.get("resolution")))
            counts["tickets"] += 1
        counts["users"] += 1
    for e in json.loads((config.DATA_DIR / "calendar.json").read_text(encoding="utf-8"))["events"]:
        db.add(CalendarEvent(start_date=_d(e["start"]), end_date=_d(e["end"]), title=e["title"], event_type=e["type"], status=e["status"], kb_ref=e.get("kb")))
        counts["calendar_events"] += 1
    db.flush()
    return counts
