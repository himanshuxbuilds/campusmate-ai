"""Fee logic: late-fee policy (Late Fees Policy v02, KB F.3.1-F.3.3) and the fee status view."""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import iso, utcnow
from app.models import FeeInstallment, FeeRecord


def late_fee_for(days_late: int, fee_amount: int = 200000, permitted_beyond_6_weeks: bool = False) -> dict[str, Any]:
    """Progressive bands: days 1-14 INR 100, 15-28 INR 200, 29-42 INR 400; beyond 6 weeks INR 2,000/day only if attendance is
    permitted; total capped at 100% of the fee."""
    d = max(0, int(days_late))
    b1, b2, b3 = min(d, 14) * 100, min(max(d - 14, 0), 14) * 200, min(max(d - 28, 0), 14) * 400
    beyond = max(d - 42, 0)
    extra = beyond * 2000 if permitted_beyond_6_weeks else 0
    total = b1 + b2 + b3 + extra
    capped = min(total, fee_amount)
    out = {"days_late": d, "band_up_to_2_weeks": b1, "band_2_to_4_weeks": b2, "band_4_to_6_weeks": b3, "days_beyond_6_weeks": beyond,
           "penalty_beyond_6_weeks": extra, "total_late_fee": capped, "capped_at_100_percent_of_fee": capped < total,
           "policy_refs": ["F.3.1", "F.3.2"]}
    if beyond > 0:
        out["warning"] = ("Payment is more than 6 weeks late. Under the Late Fees Policy the student is barred from examinations and lectures; "
                          "attendance may be permitted only in extraordinary situations with INR 2,000 per extra day [F.3.3].")
        out["policy_refs"].append("F.3.3")
    return out


def recompute_fee_record(db: Session, student_id: str) -> FeeRecord | None:
    """Keep the aggregate fee_records row consistent with the installments."""
    rec = db.execute(select(FeeRecord).where(FeeRecord.student_id == student_id)).scalar_one_or_none()
    if rec is None:
        return None
    inst = db.execute(select(FeeInstallment).where(FeeInstallment.student_id == student_id)).scalars().all()
    rec.total_fee = sum(i.amount for i in inst)
    rec.amount_paid = sum(i.amount for i in inst if i.status == "PAID")
    rec.amount_due = rec.total_fee - rec.amount_paid
    rec.status = "PAID" if rec.amount_due == 0 else ("UNPAID" if rec.amount_paid == 0 else "PARTIALLY_PAID")
    rec.updated_at = utcnow()
    db.flush()
    return rec


def fee_status(db: Session, student_id: str, today: date) -> dict[str, Any] | None:
    rec = db.execute(select(FeeRecord).where(FeeRecord.student_id == student_id)).scalar_one_or_none()
    if rec is None:
        return None
    inst = db.execute(select(FeeInstallment).where(FeeInstallment.student_id == student_id).order_by(FeeInstallment.due_date)).scalars().all()
    rows, outstanding, next_due, any_overdue = [], 0, None, False
    for i in inst:
        row = {"name": i.name, "due_date": i.due_date.isoformat(), "amount": i.amount, "status": i.status,
               "paid_on": i.paid_on.isoformat() if i.paid_on else None, "late_fee": i.late_fee, "note": i.note}
        if i.status in ("PENDING", "OVERDUE") and i.due_date < today:
            days = (today - i.due_date).days
            lf = late_fee_for(days, i.amount, permitted_beyond_6_weeks=days > 42)
            row.update(status="OVERDUE", days_overdue=days, late_fee_accrued_as_of_today=lf["total_late_fee"], beyond_6_weeks=days > 42)
            outstanding += i.amount + lf["total_late_fee"]
            any_overdue = True
        elif i.status == "PENDING":
            if next_due is None:
                next_due = {"name": i.name, "due_date": i.due_date.isoformat(), "amount": i.amount, "days_remaining": (i.due_date - today).days}
        elif i.late_fee and not i.late_fee_paid:
            outstanding += i.late_fee
            row["unpaid_late_fee"] = i.late_fee
        rows.append(row)
    return {"student_id": student_id, "academic_year": rec.academic_year, "as_of": today.isoformat(), "total_fee": rec.total_fee,
            "total_tuition": rec.total_tuition, "caution_deposit": rec.caution_deposit, "amount_paid": rec.amount_paid,
            "amount_due": rec.amount_due, "status": "OVERDUE" if any_overdue else rec.status,
            "scholarship_adjustment_total": rec.scholarship_adjustment_total, "total_paid_so_far": rec.amount_paid,
            "outstanding_now": outstanding, "next_due": next_due, "installments": rows, "policy_refs": ["F.1.2", "F.3.1", "F.3.2"],
            "record_updated": iso(rec.updated_at)}
