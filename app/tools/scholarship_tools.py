from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.models import ScholarshipRecord
from app.schemas import NoArgs
from app.services import audit_service, authorization_service as authz
from app.tools.registry import ToolContext, ToolError, register


@register("check_scholarship_status", "Scholarship application status of the signed-in student (NOT_APPLIED, SUBMITTED, UNDER_REVIEW, APPROVED, REJECTED, "
          "ADDITIONAL_DOCUMENTS_REQUIRED, WITHDRAWN) with amounts. Never decide eligibility yourself; report the record.", NoArgs)
def check_scholarship_status(ctx: ToolContext, a: NoArgs) -> dict[str, Any]:
    sid = authz.resolve_student_target(ctx.actor, a.student_id, "scholarship.read")
    r = ctx.db.execute(select(ScholarshipRecord).where(ScholarshipRecord.student_id == sid)).scalar_one_or_none()
    if r is None:
        raise ToolError("SCHOLARSHIP_RECORD_UNAVAILABLE", "No scholarship record is available for your account. Please contact the Program Office.")
    audit_service.record(ctx.db, "RECORD_ACCESS", "scholarship.read", "SUCCESS", actor=ctx.actor, target_type="scholarship_record", target_id=sid,
                         authorization_decision="ALLOWED", trace_id=ctx.trace.trace_id)
    return {"student_id": sid, "scholarship_name": r.scholarship_name, "status": r.application_status, "academic_year": r.academic_year,
            "amount_total": r.amount_total, "amount_adjusted": r.amount_adjusted, "amount_remaining": r.amount_remaining, "notes": r.notes,
            "applied_on": r.applied_on.isoformat() if r.applied_on else None, "decided_on": r.decided_on.isoformat() if r.decided_on else None,
            "policy_refs": ["G.1.1", "G.2.2", "G.2.3"]}
