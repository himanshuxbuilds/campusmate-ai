from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.schemas import DecisionBody
from app.security.deps import Auth, authenticated, http_error, staff_or_admin
from app.services import approval_service as svc

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


def _names(db: Session, reqs) -> dict[str, str]:
    codes = {r.requester_id for r in reqs}
    return {u.user_code: u.name for u in db.execute(select(User).where(User.user_code.in_(codes or {""}))).scalars()}


def _err(e: svc.ApprovalError) -> HTTPException:
    return http_error(e.http_status, e.code, e.message)


@router.get("/pending")
def pending(limit: int = 50, offset: int = 0, db: Session = Depends(get_db), auth: Auth = Depends(staff_or_admin)):
    rows = svc.list_requests(db, auth.principal, "PENDING", limit, offset)
    names = _names(db, rows)
    return {"approvals": [svc.to_dict(r, names.get(r.requester_id)) for r in rows]}


@router.get("")
def list_all(status: str | None = None, limit: int = 50, offset: int = 0, db: Session = Depends(get_db), auth: Auth = Depends(staff_or_admin)):
    if status and status not in svc.STATES:
        raise http_error(422, "INVALID_STATUS", "Unknown status filter.")
    rows = svc.list_requests(db, auth.principal, status, limit, offset)
    names = _names(db, rows)
    return {"approvals": [svc.to_dict(r, names.get(r.requester_id)) for r in rows]}


@router.get("/{request_number}")
def get_one(request_number: str, db: Session = Depends(get_db), auth: Auth = Depends(authenticated)):
    try:
        r = svc.get(db, auth.principal, request_number)
    except svc.ApprovalError as e:
        raise _err(e)
    return {"approval": svc.to_dict(r, _names(db, [r]).get(r.requester_id))}


def _decide(decision: str, request_number: str, body: DecisionBody, db: Session, auth: Auth):
    try:
        r = svc.decide(db, auth.principal, request_number, decision, body.comment)
    except svc.ApprovalError as e:
        raise _err(e)
    return {"approval": svc.to_dict(r, _names(db, [r]).get(r.requester_id))}


@router.post("/{request_number}/approve")
def approve(request_number: str, body: DecisionBody = DecisionBody(), db: Session = Depends(get_db), auth: Auth = Depends(staff_or_admin)):
    return _decide("approve", request_number, body, db, auth)


@router.post("/{request_number}/reject")
def reject(request_number: str, body: DecisionBody = DecisionBody(), db: Session = Depends(get_db), auth: Auth = Depends(staff_or_admin)):
    return _decide("reject", request_number, body, db, auth)
