"""Pydantic models: HTTP request bodies, tool arguments and the per-action `changes` of sensitive requests.
`extra="forbid"` everywhere, so unexpected or smuggled fields are rejected instead of ignored."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services import catalog

Strict = ConfigDict(extra="forbid", str_strip_whitespace=True)
TicketCategory = Literal[tuple(catalog.TICKET_CATEGORIES)]          # type: ignore[valid-type]
TicketPriority = Literal["NORMAL", "HIGH", "URGENT"]
ActionType = Literal[tuple(catalog.SENSITIVE_ACTIONS.keys())]        # type: ignore[valid-type]
MemoryKey = Literal["response_style", "preferred_language", "preferred_name", "contact_preference", "programme", "interest", "plan", "hostel_block", "support_topic"]
ProgrammeCode = Literal["PGDM-AIDS", "PGDM-BDA"]
TICKET_NUMBER_RE = r"^SR-\d{4}-\d{4}$"


# ---------------------------------------------------------------- HTTP bodies
class LoginBody(BaseModel):
    model_config = Strict
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class ChatBody(BaseModel):
    model_config = Strict                       # a smuggled `controls_enabled` / `session_id` field is a 422, not a bypass
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ConversationCreate(BaseModel):
    model_config = Strict
    title: str | None = Field(default=None, max_length=120)


class TicketCreateBody(BaseModel):
    model_config = Strict
    category: TicketCategory
    subject: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=2000)
    priority: TicketPriority = "NORMAL"
    programme: str | None = Field(default=None, max_length=32)
    related_reference: str | None = Field(default=None, max_length=60)


class DecisionBody(BaseModel):
    model_config = Strict
    comment: str = Field(default="", max_length=500)


class TicketUpdateBody(BaseModel):
    model_config = Strict
    status: Literal["OPEN", "IN_PROGRESS", "AWAITING_STUDENT", "RESOLVED", "CLOSED", "ESCALATED"] | None = None
    staff_note: str | None = Field(default=None, max_length=1000)
    assign_to_me: bool = False


class UserUpdateBody(BaseModel):
    model_config = Strict
    is_active: bool | None = None
    role: Literal["student", "staff", "admin"] | None = None


# ---------------------------------------------------------------- tool arguments
class _Tool(BaseModel):
    model_config = Strict


class NoArgs(_Tool):
    student_id: str | None = Field(default=None, max_length=64, description="Leave empty for the signed-in student. Any other student is denied by the server.")


class PolicyArgs(_Tool):
    query: str = Field(min_length=3, max_length=300, description="The student's question or key terms to search the handbook for.")
    category: str | None = Field(default=None, max_length=40, description="Optional handbook part letter (e.g. 'F' for fees) or entry type.")
    limit: int | None = Field(default=None, ge=1, le=8)


class CreateTicketArgs(TicketCreateBody):
    student_id: str | None = Field(default=None, max_length=64, description="Leave empty: tickets are always created for the signed-in student.")


class TicketStatusArgs(_Tool):
    ticket_number: str = Field(pattern=TICKET_NUMBER_RE, description="Ticket ID such as SR-2026-0101")


class SensitiveChangeArgs(_Tool):
    action_type: ActionType = Field(description="UPDATE_CONTACT | UPDATE_REFUND_BANK | SWITCH_PROGRAMME | WITHDRAW_SCHOLARSHIP | CHANGE_COURSE | FEE_CORRECTION")
    changes: dict[str, Any] = Field(description="Action-specific fields. UPDATE_CONTACT: mobile/email/address. UPDATE_REFUND_BANK: bank_name, account_last4. "
                                    "SWITCH_PROGRAMME: target_programme. WITHDRAW_SCHOLARSHIP: {}. CHANGE_COURSE: from_course, to_course. "
                                    "FEE_CORRECTION: installment_name, new_late_fee.")
    reason: str = Field(default="", max_length=500)
    student_id: str | None = Field(default=None, max_length=64)


class CalendarArgs(_Tool):
    query: str = Field(default="", max_length=100, description="e.g. 'exam', 'holiday', 'fee' or free text")
    from_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    to_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    limit: int = Field(default=8, ge=1, le=15)


class CurriculumArgs(_Tool):
    trimester: str | None = Field(default=None, pattern=r"^(I|II|III|IV|V|VI|[1-6])$", description="I-VI or 1-6; omit for all")
    programme: ProgrammeCode | None = None


class LateFeeArgs(_Tool):
    days_late: int | None = Field(default=None, ge=0, le=365)
    due_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    payment_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    fee_amount: int = Field(default=200000, ge=1, le=10_000_000)

    @model_validator(mode="after")
    def _need_one(self) -> "LateFeeArgs":
        if self.days_late is None and not self.due_date:
            raise ValueError("give days_late or due_date")
        return self


class SaveMemoryArgs(_Tool):
    key: MemoryKey
    value: str = Field(min_length=1, max_length=200)


class ForgetMemoryArgs(_Tool):
    memory_id: int | None = Field(default=None, ge=1)
    key: MemoryKey | None = None


# ---------------------------------------------------------------- `changes` payloads, one model per sensitive action
class UpdateContact(_Tool):
    mobile: str | None = Field(default=None, max_length=24)
    email: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=200)

    @field_validator("mobile")
    @classmethod
    def _mobile(cls, v):
        if v is not None and not re.fullmatch(r"\+?[0-9][0-9 \-]{7,18}[0-9]", v):
            raise ValueError("mobile number looks invalid")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        if v is not None and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", v):
            raise ValueError("email address looks invalid")
        return v

    @model_validator(mode="after")
    def _one(self) -> "UpdateContact":
        if not (self.mobile or self.email or self.address):
            raise ValueError("give a new mobile, email or address")
        return self


class UpdateRefundBank(_Tool):
    bank_name: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9 &.\-]+$")
    account_last4: str = Field(pattern=r"^\d{4}$", description="LAST 4 digits only; never the full account number")


class SwitchProgramme(_Tool):
    target_programme: ProgrammeCode


class WithdrawScholarship(_Tool):
    pass


class ChangeCourse(_Tool):
    from_course: str = Field(min_length=3, max_length=160)
    to_course: str = Field(min_length=3, max_length=160)


class FeeCorrection(_Tool):
    installment_name: str = Field(min_length=3, max_length=60)
    new_late_fee: int = Field(ge=0, le=1_000_000)


CHANGE_MODELS: dict[str, type[BaseModel]] = {"UPDATE_CONTACT": UpdateContact, "UPDATE_REFUND_BANK": UpdateRefundBank, "SWITCH_PROGRAMME": SwitchProgramme,
                                              "WITHDRAW_SCHOLARSHIP": WithdrawScholarship, "CHANGE_COURSE": ChangeCourse, "FEE_CORRECTION": FeeCorrection}


def llm_parameters(model: type[BaseModel]) -> dict[str, Any]:
    """JSON Schema for a function-calling API: titles removed, `X | null` collapsed to `X`, defaults of null dropped."""
    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            if "anyOf" in node:
                variants = [v for v in node["anyOf"] if v.get("type") != "null"]
                if len(variants) == 1:
                    merged = {**{k: v for k, v in node.items() if k != "anyOf"}, **variants[0]}
                    return clean(merged)
            out = {}
            for k, v in node.items():
                if k in ("title", "$defs") or (k == "default" and v is None):
                    continue
                out[k] = clean(v)
            return out
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node
    sch = clean(model.model_json_schema())
    sch.setdefault("type", "object")
    sch.setdefault("properties", {})
    return sch
