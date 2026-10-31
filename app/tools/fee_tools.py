from __future__ import annotations

from typing import Any

from app.schemas import NoArgs
from app.services import audit_service, authorization_service as authz, fee_service
from app.tools.registry import ToolContext, ToolError, register


@register("check_fee_status", "Fee status of the signed-in student: installments paid/pending/overdue, late fees accrued, amount paid/due, outstanding amount, next due date. "
          "Use for any question about the student's own fees or dues. Never guess fee data.", NoArgs)
def check_fee_status(ctx: ToolContext, a: NoArgs) -> dict[str, Any]:
    sid = authz.resolve_student_target(ctx.actor, a.student_id, "fees.read")
    data = fee_service.fee_status(ctx.db, sid, ctx.today)
    if data is None:
        raise ToolError("FEE_RECORD_UNAVAILABLE", "No fee record is available for your account, so I can't state your fee status. Please contact Accounts.")
    audit_service.record(ctx.db, "RECORD_ACCESS", "fees.read", "SUCCESS", actor=ctx.actor, target_type="fee_record", target_id=sid,
                         authorization_decision="ALLOWED", trace_id=ctx.trace.trace_id)
    return data
