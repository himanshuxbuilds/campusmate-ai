import json
import threading
from datetime import timedelta
from unittest import mock

import pytest
from sqlalchemy import func, select

from app import db
from app.db import utcnow
from app.models import (ApprovalRequest, AuditEvent, CourseRegistration, FeeInstallment, FeeRecord, ScholarshipRecord, StudentRecord, User)
from app.security.context import Principal
from app.services import approval_service as svc, authorization_service as authz, catalog
from conftest import audit_rows, pending

ME = Principal("S2025AI001", "Aarav Mehta", "student", "PGDM-AIDS")
OFFICE = Principal("program.office", "Program Office", "staff", None, {"approval_actions": catalog.PROGRAM_OFFICE_ACTIONS})
ACCOUNTS = Principal("accounts", "Accounts", "staff", None, {"approval_actions": catalog.ACCOUNTS_ACTIONS})
ADMIN = Principal("admin.campus", "Admin", "admin")


def make(session, action="UPDATE_CONTACT", changes=None, who=ME):
    r = svc.create_request(session, who, action, changes if changes is not None else {"mobile": "+91 98765 43210"}, "test reason", "TR-TEST")
    session.commit()
    return r


def rec(session, sid="S2025AI001"):
    session.expire_all()
    return session.execute(select(StudentRecord).where(StudentRecord.student_id == sid)).scalar_one()


# ---------------------------------------------------------------- creation
def test_request_is_pending_hashed_expiring_and_audited_without_values(session):
    r = make(session)
    assert r.status == "PENDING" and len(r.proposal_hash) == 64 and r.request_number.startswith("APR-")
    assert timedelta(hours=71) < r.expires_at - utcnow() <= timedelta(hours=72, seconds=5)
    ev = audit_rows(event_type="SENSITIVE_REQUEST")[0]
    assert ev.outcome == "PENDING" and ev.approval_status == "PENDING" and ev.details["fields"] == ["mobile"] and "98765" not in json.dumps(ev.details) + (ev.reason or "")


def test_only_students_request_changes_to_their_own_record(session):
    with pytest.raises(authz.Forbidden):
        svc.create_request(session, OFFICE, "UPDATE_CONTACT", {"mobile": "+91 98765 43210"}, "", None)


def test_state_machine_terminal_states_have_no_exits():
    assert set(svc.STATES) == {"PENDING", "APPROVED", "REJECTED", "EXECUTED", "FAILED", "EXPIRED"} == set(svc.TRANSITIONS)
    assert all(svc.TRANSITIONS[s] == set() for s in ("REJECTED", "EXECUTED", "FAILED", "EXPIRED"))
    assert svc.TRANSITIONS["APPROVED"] == {"EXECUTED", "FAILED"} and "EXECUTED" not in svc.TRANSITIONS["PENDING"]


# ---------------------------------------------------------------- rejection
def test_rejection_leaves_the_record_untouched_and_is_final(session):
    before = rec(session).mobile
    r = make(session)
    out = svc.decide(session, OFFICE, r.request_number, "reject", "ID not verified"); session.commit()
    assert out.status == "REJECTED" and out.approver_id == "program.office" and out.approval_comment == "ID not verified" and out.executed_at is None and rec(session).mobile == before
    with pytest.raises(svc.ApprovalError) as e:
        svc.decide(session, OFFICE, r.request_number, "approve")
    assert e.value.code == "ALREADY_PROCESSED" and rec(session).mobile == before
    session.rollback()                                                                  # SQLite: release the write lock before a second connection reads
    assert audit_rows(event_type="APPROVAL_DECISION")[-1].approval_status == "REJECTED"


# ---------------------------------------------------------------- approval executes each action for real
def test_approve_contact_details(session):
    r = make(session, changes={"mobile": "+91 91111 22222", "address": "1 New Road, Surat", "email": "aarav.new@example.com"})
    out = svc.decide(session, OFFICE, r.request_number, "approve"); session.commit()
    assert out.status == "EXECUTED" and out.executed_at and rec(session).mobile == "+91 91111 22222" and rec(session).address == "1 New Road, Surat"
    assert session.execute(select(User.email).where(User.user_code == "S2025AI001")).scalar_one() == "aarav.new@example.com"


def test_approve_refund_bank_stores_only_last4(session):
    out = svc.decide(session, ACCOUNTS, make(session, "UPDATE_REFUND_BANK", {"bank_name": "HDFC Bank", "account_last4": "4321"}).request_number, "approve")
    assert out.status == "EXECUTED" and rec(session).refund_bank_masked == "HDFC Bank ****4321"


def test_approve_programme_switch_updates_record_and_user(session):
    out = svc.decide(session, OFFICE, make(session, "SWITCH_PROGRAMME", {"target_programme": "PGDM-BDA"}).request_number, "approve"); session.commit()
    assert out.status == "EXECUTED" and rec(session).programme == "PGDM-BDA" and session.execute(select(User.programme).where(User.user_code == "S2025AI001")).scalar_one() == "PGDM-BDA"


def test_approve_scholarship_withdrawal(session):
    out = svc.decide(session, OFFICE, make(session, "WITHDRAW_SCHOLARSHIP", {}).request_number, "approve"); session.commit()
    assert out.status == "EXECUTED" and session.execute(select(ScholarshipRecord.application_status).where(ScholarshipRecord.student_id == "S2025AI001")).scalar_one() == "WITHDRAWN"


def test_approve_elective_change_drops_old_and_registers_new(session):
    out = svc.decide(session, OFFICE, make(session, "CHANGE_COURSE", {"from_course": "Entrepreneurship Essentials", "to_course": "HR Analytics"}).request_number, "approve"); session.commit()
    regs = {(c.course_name, c.status) for c in session.execute(select(CourseRegistration).where(CourseRegistration.student_id == "S2025AI001")).scalars()}
    assert out.status == "EXECUTED" and ("Entrepreneurship Essentials", "DROPPED") in regs and ("HR Analytics", "REGISTERED") in regs


def test_approve_fee_correction_keeps_fee_record_consistent(session):
    inst = session.execute(select(FeeInstallment).where(FeeInstallment.student_id == "S2025AI001", FeeInstallment.late_fee.is_not(None))).scalars().first()
    name = inst.name if inst else "4th installment"
    out = svc.decide(session, ACCOUNTS, make(session, "FEE_CORRECTION", {"installment_name": name, "new_late_fee": 0}).request_number, "approve"); session.commit()
    f = session.execute(select(FeeRecord).where(FeeRecord.student_id == "S2025AI001")).scalar_one()
    assert out.status == "EXECUTED" and f.total_fee == f.amount_paid + f.amount_due


def test_each_approved_change_is_audited_exactly_once_with_field_names_only(session):
    r = make(session, changes={"mobile": "+91 91111 22222"})
    svc.decide(session, OFFICE, r.request_number, "approve"); session.commit()
    mods = [e for e in audit_rows(event_type="RECORD_MODIFIED") if e.details["request_number"] == r.request_number]
    assert len(mods) == 1 and mods[0].approval_status == "EXECUTED" and mods[0].approver_id == "program.office" and "91111" not in json.dumps(mods[0].details)


# ---------------------------------------------------------------- who may decide
def test_requester_can_never_decide_their_own_request(session):
    r = make(session)
    for who in (ME, Principal("S2025AI001", "x", "staff", None, {"approval_actions": ["UPDATE_CONTACT"]}), Principal("S2025AI001", "x", "admin")):
        with pytest.raises(authz.Forbidden):
            svc.decide(session, who, r.request_number, "approve")
    assert session.execute(select(ApprovalRequest.status).where(ApprovalRequest.id == r.id)).scalar_one() == "PENDING"


def test_approver_must_be_in_scope_and_out_of_scope_staff_learn_nothing(session):
    r = make(session)                                                                  # UPDATE_CONTACT is Program Office scope
    with pytest.raises(svc.ApprovalError) as e:
        svc.decide(session, ACCOUNTS, r.request_number, "approve")
    assert e.value.code == "NOT_FOUND" and e.value.http_status == 404
    assert svc.list_requests(session, ACCOUNTS) == [] and [x.id for x in svc.list_requests(session, OFFICE)] == [r.id] and len(svc.list_requests(session, ADMIN)) == 1
    assert svc.decide(session, ADMIN, r.request_number, "reject").status == "REJECTED"


def test_unknown_request_number(session):
    with pytest.raises(svc.ApprovalError) as e:
        svc.decide(session, OFFICE, "APR-2099-0001", "approve")
    assert e.value.http_status == 404


# ---------------------------------------------------------------- expiry, integrity, failure, atomicity
def test_expired_requests_cannot_be_approved(session):
    r = make(session)
    r.expires_at = utcnow() - timedelta(minutes=1); session.commit()
    before = rec(session).mobile
    with pytest.raises(svc.ApprovalError) as e:
        svc.decide(session, OFFICE, r.request_number, "approve")
    assert e.value.code == "EXPIRED" and e.value.http_status == 410
    assert session.execute(select(ApprovalRequest.status).where(ApprovalRequest.id == r.id)).scalar_one() == "EXPIRED" and rec(session).mobile == before
    with pytest.raises(svc.ApprovalError):
        svc.decide(session, OFFICE, r.request_number, "approve")


def test_listing_marks_overdue_requests_expired(session):
    r = make(session); r.expires_at = utcnow() - timedelta(seconds=5); session.commit()
    assert svc.list_requests(session, OFFICE, "PENDING") == [] and svc.list_requests(session, OFFICE, "EXPIRED")[0].id == r.id


def test_a_tampered_proposal_is_never_executed(session):
    r = make(session, changes={"mobile": "+91 91111 22222"})
    before = rec(session).mobile
    r.proposed_change = {"mobile": "+91 00000 99999"}; session.commit()                  # altered after submission
    with pytest.raises(svc.ApprovalError) as e:
        svc.decide(session, OFFICE, r.request_number, "approve")
    assert e.value.code == "INTEGRITY" and rec(session).mobile == before
    assert session.execute(select(ApprovalRequest.status).where(ApprovalRequest.id == r.id)).scalar_one() == "FAILED"


def test_failed_execution_is_recorded_as_failed_and_changes_nothing(session):
    r = make(session)
    before = rec(session).mobile
    with mock.patch.dict(svc.EXECUTORS, {"UPDATE_CONTACT": mock.Mock(side_effect=RuntimeError("disk full"))}):
        out = svc.decide(session, OFFICE, r.request_number, "approve"); session.commit()
    assert out.status == "FAILED" and "RuntimeError" in out.execution_result and "disk full" not in out.execution_result and out.executed_at is None
    assert rec(session).mobile == before
    session.rollback()
    assert not [e for e in audit_rows(event_type="RECORD_MODIFIED") if e.details["request_number"] == r.request_number]
    assert audit_rows(event_type="APPROVAL_DECISION")[-1].outcome == "FAILURE"
    with pytest.raises(svc.ApprovalError):
        svc.decide(session, OFFICE, r.request_number, "approve")                          # a failed request is final; the student files a new one


def test_partial_writes_are_rolled_back_when_execution_fails_midway(session):
    r = make(session)
    before = rec(session).mobile
    def half_then_boom(db_, req):
        rec(db_).mobile = "+91 77777 77777"; db_.flush()
        raise RuntimeError("crash after writing")
    with mock.patch.dict(svc.EXECUTORS, {"UPDATE_CONTACT": half_then_boom}):
        out = svc.decide(session, OFFICE, r.request_number, "approve"); session.commit()
    assert out.status == "FAILED" and rec(session).mobile == before


def test_duplicate_decisions_cannot_re_execute(session):
    r = make(session)
    svc.decide(session, OFFICE, r.request_number, "approve"); session.commit()
    for d in ("approve", "reject", "approve"):
        with pytest.raises(svc.ApprovalError):
            svc.decide(session, OFFICE, r.request_number, d)
    session.rollback()
    assert len([e for e in audit_rows(event_type="RECORD_MODIFIED")]) == 1


def test_simultaneous_approvals_execute_exactly_once(database):
    with db.session_scope() as s:
        n = svc.create_request(s, ME, "UPDATE_CONTACT", {"mobile": "+91 91111 22222"}, "", None).request_number
    out, barrier = [], threading.Barrier(4)
    def go():
        barrier.wait()
        try:
            with db.session_scope() as s:
                svc.decide(s, OFFICE, n, "approve"); out.append("ok")
        except svc.ApprovalError as e:
            out.append(e.code)
    th = [threading.Thread(target=go) for _ in range(4)]; [t.start() for t in th]; [t.join() for t in th]
    assert sorted(out) == ["ALREADY_PROCESSED"] * 3 + ["ok"]
    with db.session_scope() as s:
        assert s.execute(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "RECORD_MODIFIED")).scalar_one() == 1


# ---------------------------------------------------------------- over HTTP
def test_http_workflow_end_to_end(aarav, staff):
    n = pending(aarav, "Change my mobile number to +91 91234 56789")
    assert aarav.get("/api/student/profile").json()["mobile"] != "+91 91234 56789"       # still unchanged while pending
    assert aarav.post(f"/api/approvals/{n}/approve").status_code == 403
    r = staff.post(f"/api/approvals/{n}/approve", json={"comment": "verified by call"})
    assert r.status_code == 200 and r.json()["approval"]["status"] == "EXECUTED" and r.json()["approval"]["approval_comment"] == "verified by call"
    assert aarav.get("/api/student/profile").json()["mobile"] == "+91 91234 56789"
    assert staff.post(f"/api/approvals/{n}/approve").status_code == 409 and staff.post(f"/api/approvals/{n}/reject").status_code == 409
    assert aarav.get("/api/student/approvals").json()["approvals"][0]["status"] == "EXECUTED"


def test_http_reject_expired_and_unknown(aarav, staff, session):
    n = pending(aarav)
    assert staff.post(f"/api/approvals/{n}/reject", json={"comment": "no"}).json()["approval"]["status"] == "REJECTED"
    m = pending(aarav, "Change my address to 2 Lake Road, Surat")
    session.execute(select(ApprovalRequest).where(ApprovalRequest.request_number == m)).scalar_one().expires_at = utcnow() - timedelta(seconds=1); session.commit()
    assert staff.post(f"/api/approvals/{m}/approve").status_code == 410
    assert staff.post("/api/approvals/APR-2099-0001/approve").status_code == 404 and staff.get("/api/approvals?status=BOGUS").status_code == 422


def test_http_pending_card_shows_requested_changes_to_the_approver(aarav, staff):
    n = pending(aarav, "Change my address to 9 Hill Road, Surat")
    card = next(a for a in staff.get("/api/approvals/pending").json()["approvals"] if a["request_number"] == n)
    assert card["requested_changes"] == {"address": "9 Hill Road, Surat"} and card["requester_id"] == "S2025AI001" and card["requester_name"] == "Aarav Mehta" and card["action_description"]
