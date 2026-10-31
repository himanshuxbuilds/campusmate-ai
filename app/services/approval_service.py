"""Human-approval workflow for sensitive changes. Enforced entirely in application code.

State machine (documented in docs/approval_workflow.md, exercised by tests/test_approvals.py):

    PENDING --reject--------------------------> REJECTED            (terminal, nothing executed)
    PENDING --approve--> APPROVED --execute ok-> EXECUTED           (terminal, change applied exactly once)
                                  \\-execute error-> FAILED          (terminal, change NOT applied, nothing half-written)
    PENDING --time passes / decide too late-----> EXPIRED            (terminal)
    PENDING --proposal hash mismatch at decision-> FAILED            (terminal, integrity check)

Safety properties: the requester can never decide their own request; approvers act only inside their scope; a decision
is an atomic compare-and-set on `status = 'PENDING'` so concurrent or repeated approvals cannot execute twice; the
proposed change is hashed at creation and re-verified before execution; the record update and the status change commit
together in one database transaction.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import config
from app.db import iso, utcnow
from app.models import (ApprovalRequest, CourseRegistration, FeeInstallment, ScholarshipRecord, StudentRecord, User)
from app.schemas import CHANGE_MODELS
from app.security.context import Principal
from app.services import audit_service, authorization_service as authz, catalog
from app.services.fee_service import recompute_fee_record

STATES = ("PENDING", "APPROVED", "REJECTED", "EXECUTED", "FAILED", "EXPIRED")
TRANSITIONS = {"PENDING": {"APPROVED", "REJECTED", "EXPIRED", "FAILED"}, "APPROVED": {"EXECUTED", "FAILED"}, "REJECTED": set(), "EXECUTED": set(), "FAILED": set(), "EXPIRED": set()}


class ApprovalError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


class ExecutionError(Exception):
    pass


def proposal_hash(action_type: str, requester_id: str, target_type: str, target_id: str, change: dict[str, Any]) -> str:
    canon = json.dumps({"a": action_type, "r": requester_id, "tt": target_type, "ti": target_id, "c": change}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def _next_number(db: Session) -> str:
    n = db.execute(select(ApprovalRequest.id).order_by(ApprovalRequest.id.desc()).limit(1)).scalar() or 0
    return f"APR-{utcnow().year}-{n + 1:04d}"


def validate_changes(action_type: str, changes: dict[str, Any]) -> dict[str, Any]:
    model = CHANGE_MODELS.get(action_type)
    if model is None:
        raise ApprovalError("UNKNOWN_ACTION", "That change type is not supported.", 422)
    try:
        return {k: v for k, v in model(**(changes or {})).model_dump().items() if v is not None}
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(map(str, x['loc'])) or 'changes'}: {x['msg']}" for x in e.errors()[:4])
        raise ApprovalError("INVALID_CHANGE", f"The requested change is not valid ({msgs}).", 422)


def _precheck(db: Session, student_id: str, action_type: str, ch: dict[str, Any]) -> None:
    """Business validation at request time (re-checked again at execution)."""
    if action_type == "SWITCH_PROGRAMME":
        rec = db.execute(select(StudentRecord).where(StudentRecord.student_id == student_id)).scalar_one_or_none()
        if rec and rec.programme == ch["target_programme"]:
            raise ApprovalError("NO_CHANGE", "You are already enrolled in that programme.", 422)
    elif action_type == "WITHDRAW_SCHOLARSHIP":
        sch = db.execute(select(ScholarshipRecord).where(ScholarshipRecord.student_id == student_id)).scalar_one_or_none()
        if sch is None or sch.application_status in ("NOT_APPLIED", "WITHDRAWN"):
            raise ApprovalError("NOTHING_TO_WITHDRAW", "There is no active scholarship or application to withdraw.", 422)
    elif action_type == "CHANGE_COURSE":
        reg = db.execute(select(CourseRegistration).where(CourseRegistration.student_id == student_id, CourseRegistration.course_name == ch["from_course"],
                                                          CourseRegistration.status == "REGISTERED")).scalar_one_or_none()
        if reg is None:
            raise ApprovalError("COURSE_NOT_REGISTERED", f"You are not registered for '{ch['from_course']}'.", 422)
        if reg.kind != "elective":
            raise ApprovalError("CORE_COURSE", "Year 1 and core courses are compulsory and cannot be swapped; only registered electives can change.", 422)
        if ch["to_course"] not in catalog.ELECTIVE_BASKET:
            raise ApprovalError("NOT_IN_BASKET", "That course is not in the elective basket: " + ", ".join(catalog.ELECTIVE_BASKET) + ".", 422)
        dup = db.execute(select(CourseRegistration.id).where(CourseRegistration.student_id == student_id, CourseRegistration.course_name == ch["to_course"],
                                                             CourseRegistration.status == "REGISTERED")).first()
        if dup:
            raise ApprovalError("ALREADY_REGISTERED", "You are already registered for that course.", 422)
    elif action_type == "FEE_CORRECTION":
        inst = db.execute(select(FeeInstallment).where(FeeInstallment.student_id == student_id, FeeInstallment.name == ch["installment_name"])).scalar_one_or_none()
        if inst is None:
            raise ApprovalError("INSTALLMENT_NOT_FOUND", "No installment with that name exists on your fee record.", 422)


def create_request(db: Session, requester: Principal, action_type: str, changes: dict[str, Any], reason: str, trace_id: str | None) -> ApprovalRequest:
    if requester.role != "student":
        raise authz.Forbidden("approval.create", "approval_request", None, "only students request changes to their own record")
    ch = validate_changes(action_type, changes)
    _precheck(db, requester.user_code, action_type, ch)
    spec = catalog.SENSITIVE_ACTIONS[action_type]
    tt, ti = spec["resource"], requester.user_code
    now = utcnow()
    req = ApprovalRequest(request_number=_next_number(db), requester_id=requester.user_code, action_type=action_type, target_resource_type=tt,
                          target_resource_id=ti, proposed_change=ch, request_reason=(reason or "").strip()[:500], status="PENDING",
                          proposal_hash=proposal_hash(action_type, requester.user_code, tt, ti, ch), trace_id=trace_id, created_at=now,
                          expires_at=now + timedelta(hours=config.APPROVAL_TTL_HOURS))
    db.add(req)
    db.flush()
    audit_service.record(db, "SENSITIVE_REQUEST", f"approval.create:{action_type}", "PENDING", actor=requester, target_type=tt, target_id=ti,
                         authorization_decision="ALLOWED", approval_status="PENDING", reason=spec["label"], trace_id=trace_id,
                         details={"request_number": req.request_number, "fields": sorted(ch.keys())})       # field NAMES only, never values
    return req


# ------------------------------------------------------------------------------------------------ executors
def _student_rec(db: Session, sid: str) -> StudentRecord:
    rec = db.execute(select(StudentRecord).where(StudentRecord.student_id == sid)).scalar_one_or_none()
    if rec is None:
        raise ExecutionError("student record missing")
    return rec


def _ex_contact(db: Session, req: ApprovalRequest) -> str:
    ch, rec = req.proposed_change, _student_rec(db, req.target_resource_id)
    if "mobile" in ch: rec.mobile = ch["mobile"]
    if "address" in ch: rec.address = ch["address"]
    if "email" in ch:
        u = db.execute(select(User).where(User.user_code == req.target_resource_id)).scalar_one()
        if db.execute(select(User.id).where(User.email == ch["email"], User.user_code != u.user_code)).first():
            raise ExecutionError("email already in use")
        u.email = ch["email"]
    db.flush()
    return "contact details updated: " + ", ".join(sorted(ch.keys()))


def _ex_bank(db: Session, req: ApprovalRequest) -> str:
    ch = req.proposed_change
    _student_rec(db, req.target_resource_id).refund_bank_masked = f"{ch['bank_name']} ****{ch['account_last4']}"
    db.flush()
    return "refund bank account updated"


def _ex_switch(db: Session, req: ApprovalRequest) -> str:
    tp, rec = req.proposed_change["target_programme"], _student_rec(db, req.target_resource_id)
    if rec.programme == tp:
        raise ExecutionError("already in that programme")
    rec.programme, rec.programme_name = tp, catalog.PROGRAMMES[tp]
    db.execute(update(User).where(User.user_code == req.target_resource_id).values(programme=tp))
    db.flush()
    return f"programme switched to {tp}"


def _ex_withdraw(db: Session, req: ApprovalRequest) -> str:
    sch = db.execute(select(ScholarshipRecord).where(ScholarshipRecord.student_id == req.target_resource_id)).scalar_one_or_none()
    if sch is None or sch.application_status in ("NOT_APPLIED", "WITHDRAWN"):
        raise ExecutionError("nothing to withdraw")
    sch.application_status = "WITHDRAWN"
    sch.notes = ((sch.notes or "") + " Withdrawn at the student's request after approval.").strip()
    db.flush()
    return "scholarship withdrawn"


def _ex_course(db: Session, req: ApprovalRequest) -> str:
    ch = req.proposed_change
    reg = db.execute(select(CourseRegistration).where(CourseRegistration.student_id == req.target_resource_id, CourseRegistration.course_name == ch["from_course"],
                                                      CourseRegistration.status == "REGISTERED", CourseRegistration.kind == "elective")).scalar_one_or_none()
    if reg is None:
        raise ExecutionError("elective is no longer registered")
    reg.status = "DROPPED"
    db.add(CourseRegistration(student_id=req.target_resource_id, course_name=ch["to_course"], trimester=reg.trimester, kind="elective", credits=reg.credits))
    db.flush()
    return f"elective changed: {ch['from_course']} -> {ch['to_course']}"


def _ex_fee(db: Session, req: ApprovalRequest) -> str:
    ch = req.proposed_change
    inst = db.execute(select(FeeInstallment).where(FeeInstallment.student_id == req.target_resource_id, FeeInstallment.name == ch["installment_name"])).scalar_one_or_none()
    if inst is None:
        raise ExecutionError("installment not found")
    inst.late_fee = ch["new_late_fee"]
    inst.late_fee_paid = inst.late_fee_paid if ch["new_late_fee"] else True
    inst.note = ((inst.note or "") + " Late fee corrected after approval.").strip()
    db.flush()
    recompute_fee_record(db, req.target_resource_id)
    return f"late fee on {inst.name} corrected"


EXECUTORS: dict[str, Callable[[Session, ApprovalRequest], str]] = {
    "UPDATE_CONTACT": _ex_contact, "UPDATE_REFUND_BANK": _ex_bank, "SWITCH_PROGRAMME": _ex_switch, "WITHDRAW_SCHOLARSHIP": _ex_withdraw,
    "CHANGE_COURSE": _ex_course, "FEE_CORRECTION": _ex_fee}


# ------------------------------------------------------------------------------------------------ decision
def _cas(db: Session, req: ApprovalRequest, new_status: str, **values: Any) -> None:
    """Atomic PENDING -> new_status. Exactly one concurrent caller can win."""
    res = db.execute(update(ApprovalRequest).where(ApprovalRequest.id == req.id, ApprovalRequest.status == "PENDING").values(status=new_status, **values))
    if res.rowcount != 1:
        db.rollback()
        cur = db.execute(select(ApprovalRequest.status).where(ApprovalRequest.id == req.id)).scalar_one()
        raise ApprovalError("ALREADY_PROCESSED", f"This request is already {cur}.")
    db.expire(req)


def expire_due(db: Session) -> int:
    n = 0
    for r in db.execute(select(ApprovalRequest).where(ApprovalRequest.status == "PENDING", ApprovalRequest.expires_at <= utcnow())).scalars().all():
        db.execute(update(ApprovalRequest).where(ApprovalRequest.id == r.id, ApprovalRequest.status == "PENDING").values(status="EXPIRED"))
        audit_service.record(db, "APPROVAL_DECISION", "approval.expire", "FAILURE", actor_id="system", actor_role="system", target_type=r.target_resource_type,
                             target_id=r.target_resource_id, approval_status="EXPIRED", reason="not decided before expiry", details={"request_number": r.request_number})
        n += 1
    db.flush()
    return n


def decide(db: Session, actor: Principal, request_number: str, decision: str, comment: str = "") -> ApprovalRequest:
    assert decision in ("approve", "reject")
    req = db.execute(select(ApprovalRequest).where(ApprovalRequest.request_number == request_number)).scalar_one_or_none()
    if req is None:
        raise ApprovalError("NOT_FOUND", "Approval request not found.", 404)
    if not authz.can_view_approval(actor, req) and actor.role == "staff":
        raise ApprovalError("NOT_FOUND", "Approval request not found.", 404)        # out-of-scope staff learn nothing
    ok, why = authz.can_decide_approval(actor, req)
    if not ok:
        raise authz.Forbidden(f"approval.{decision}", "approval_request", request_number, why)
    now = utcnow()
    base = dict(actor=actor, target_type=req.target_resource_type, target_id=req.target_resource_id, approver_id=actor.user_code, trace_id=req.trace_id)
    if req.status == "PENDING" and now >= req.expires_at:
        _cas(db, req, "EXPIRED")
        audit_service.record(db, "APPROVAL_DECISION", f"approval.{decision}", "FAILURE", authorization_decision="ALLOWED", approval_status="EXPIRED",
                             reason="request expired", details={"request_number": request_number}, commit=True, **base)
        raise ApprovalError("EXPIRED", "This request has expired and can no longer be approved.", 410)
    if req.status != "PENDING":
        raise ApprovalError("ALREADY_PROCESSED", f"This request is already {req.status}.")
    if proposal_hash(req.action_type, req.requester_id, req.target_resource_type, req.target_resource_id, req.proposed_change) != req.proposal_hash:
        _cas(db, req, "FAILED", approver_id=actor.user_code, decided_at=now, execution_result="integrity check failed: the proposal was altered after submission")
        audit_service.record(db, "APPROVAL_DECISION", f"approval.{decision}", "FAILURE", authorization_decision="ALLOWED", approval_status="FAILED",
                             reason="proposal hash mismatch", details={"request_number": request_number}, commit=True, **base)
        raise ApprovalError("INTEGRITY", "The request failed its integrity check and was not executed.", 409)
    comment = (comment or "").strip()[:500]
    if decision == "reject":
        _cas(db, req, "REJECTED", approver_id=actor.user_code, decided_at=now, approval_comment=comment, execution_result="not executed")
        audit_service.record(db, "APPROVAL_DECISION", "approval.reject", "SUCCESS", authorization_decision="ALLOWED", approval_status="REJECTED",
                             reason="rejected by approver", details={"request_number": request_number}, **base)
        db.refresh(req)
        return req
    _cas(db, req, "APPROVED", approver_id=actor.user_code, decided_at=now, approval_comment=comment)
    sp = db.begin_nested()                                   # execute inside a savepoint: a failure leaves NO partial change behind
    try:
        result = EXECUTORS[req.action_type](db, req)
        sp.commit()
    except Exception as e:
        sp.rollback()
        db.execute(update(ApprovalRequest).where(ApprovalRequest.id == req.id).values(status="FAILED", execution_result=f"execution failed: {type(e).__name__}"))
        audit_service.record(db, "APPROVAL_DECISION", "approval.approve", "FAILURE", authorization_decision="ALLOWED", approval_status="FAILED",
                             reason=f"execution failed ({type(e).__name__})", details={"request_number": request_number}, **base)
        db.refresh(req)
        return req
    db.execute(update(ApprovalRequest).where(ApprovalRequest.id == req.id).values(status="EXECUTED", executed_at=utcnow(), execution_result=result))
    audit_service.record(db, "RECORD_MODIFIED", f"approval.execute:{req.action_type}", "SUCCESS", authorization_decision="ALLOWED", approval_status="EXECUTED",
                         reason=result[:150], details={"request_number": request_number, "fields": sorted(req.proposed_change.keys())}, **base)
    audit_service.record(db, "APPROVAL_DECISION", "approval.approve", "SUCCESS", authorization_decision="ALLOWED", approval_status="EXECUTED",
                         reason="approved and executed", details={"request_number": request_number}, **base)
    db.refresh(req)
    return req


# ------------------------------------------------------------------------------------------------ queries
def get(db: Session, actor: Principal, request_number: str) -> ApprovalRequest:
    req = db.execute(select(ApprovalRequest).where(ApprovalRequest.request_number == request_number)).scalar_one_or_none()
    if req is None or not authz.can_view_approval(actor, req):
        raise ApprovalError("NOT_FOUND", "Approval request not found.", 404)       # same answer for "missing" and "not yours"
    return req


def list_requests(db: Session, actor: Principal, status: str | None = None, limit: int = 50, offset: int = 0) -> list[ApprovalRequest]:
    expire_due(db)
    stmt = select(ApprovalRequest)
    if actor.role == "student":
        stmt = stmt.where(ApprovalRequest.requester_id == actor.user_code)
    elif actor.role == "staff":
        stmt = stmt.where(ApprovalRequest.action_type.in_((actor.staff_scope or {}).get("approval_actions", []) or ["__none__"]))
    if status:
        stmt = stmt.where(ApprovalRequest.status == status)
    return list(db.execute(stmt.order_by(ApprovalRequest.id.desc()).limit(max(1, min(limit, 200))).offset(max(0, offset))).scalars())


def to_dict(r: ApprovalRequest, requester_name: str | None = None) -> dict[str, Any]:
    spec = catalog.SENSITIVE_ACTIONS.get(r.action_type, {})
    return {"request_number": r.request_number, "action_type": r.action_type, "action_description": spec.get("label", r.action_type), "status": r.status,
            "requester_id": r.requester_id, "requester_name": requester_name, "target_resource_type": r.target_resource_type, "target_resource_id": r.target_resource_id,
            "requested_changes": r.proposed_change, "reason": r.request_reason, "approver_id": r.approver_id,
            "approval_comment": r.approval_comment, "execution_result": r.execution_result, "created_at": iso(r.created_at), "expires_at": iso(r.expires_at),
            "decided_at": iso(r.decided_at), "executed_at": iso(r.executed_at)}
