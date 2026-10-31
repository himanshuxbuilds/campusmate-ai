from __future__ import annotations

from typing import Any

from app.schemas import SensitiveChangeArgs
from app.services import approval_service, authorization_service as authz, catalog
from app.tools.registry import ToolContext, ToolError, register


@register("request_sensitive_change", "Request a SENSITIVE change to the student's own record. This NEVER applies the change: it files an approval request that a staff member must "
          "approve. Actions: UPDATE_CONTACT (mobile/email/address), UPDATE_REFUND_BANK (bank_name, account_last4 only), SWITCH_PROGRAMME (target_programme), "
          "WITHDRAW_SCHOLARSHIP, CHANGE_COURSE (from_course, to_course: electives only), FEE_CORRECTION (installment_name, new_late_fee). "
          "Call only after the student has confirmed the exact change.", SensitiveChangeArgs, sensitive=True)
def request_sensitive_change(ctx: ToolContext, a: SensitiveChangeArgs) -> dict[str, Any]:
    authz.resolve_student_target(ctx.actor, a.student_id, f"approval.create:{a.action_type}")      # another student's record -> Forbidden
    try:
        req = approval_service.create_request(ctx.db, ctx.actor, a.action_type, a.changes, a.reason, ctx.trace.trace_id)
    except approval_service.ApprovalError as e:
        raise ToolError(e.code, e.message)
    return {"status": "PENDING_APPROVAL", "request_number": req.request_number, "action_type": req.action_type,
            "action_description": catalog.SENSITIVE_ACTIONS[req.action_type]["label"], "requested_changes": req.proposed_change,
            "expires_at": approval_service.to_dict(req)["expires_at"], "applied": False,
            "message": "This sensitive change has NOT been applied. It was sent to the Program Office for human approval and will change your record only if approved.",
            "policy_refs": ["N.1.2"]}
