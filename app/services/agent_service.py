"""Agent orchestration: one chat request = authenticate (done by the route) -> redact -> own the conversation -> select memory ->
build a budgeted context -> bounded model/tool loop -> persist -> trace + audit.

The model proposes; the backend disposes. Every tool call is re-validated, role-checked and ownership-checked in code
(app/tools/registry.py + authorization_service). Tool results are returned to the model as data. Failures never fabricate results.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import utcnow
from app.logging_config import logger
from app.models import Conversation, ConversationMessage
from app.security.context import Principal
from app.security.redaction import redact_secrets
from app.services import approval_service, audit_service, context_builder, memory_service
from app.services.clock import today
from app.services.llm_client import LLMError, ToolCall, get_client, provider_label
from app.services.trace_service import TraceRecorder
from app.tools import ToolContext, run_tool, tool_specs
from app.tools.registry import err

Emit = Callable[[dict[str, Any]], None]
STATUS_TEXT = {"search_student_policy": "Searching the handbook", "check_fee_status": "Checking your fee record", "check_scholarship_status": "Checking your scholarship record",
               "get_student_profile": "Loading your profile", "create_support_ticket": "Creating a support ticket", "get_ticket_status": "Looking up the ticket",
               "request_sensitive_change": "Filing an approval request", "get_current_academic_calendar": "Checking the academic calendar", "get_curriculum": "Loading the curriculum",
               "compute_late_fee": "Calculating the late fee", "save_memory": "Saving a preference", "forget_memory": "Updating your saved preferences"}


class AgentError(Exception):
    def __init__(self, code: str, http_status: int, message: str, retryable: bool, trace_id: str | None = None, conversation_id: str | None = None) -> None:
        super().__init__(message)
        self.code, self.http_status, self.message, self.retryable = code, http_status, message, retryable
        self.trace_id, self.conversation_id = trace_id, conversation_id


@dataclass
class AgentResult:
    reply: str
    conversation_id: str
    user_message_id: int
    assistant_message_id: int
    trace: dict[str, Any]
    sources: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    tickets: list[str] = field(default_factory=list)
    redactions: list[str] = field(default_factory=list)
    outcome: str = "answered"
    context: dict[str, Any] = field(default_factory=dict)


def _title(text: str) -> str:
    t = " ".join(text.split())
    return (t[:57] + "...") if len(t) > 60 else (t or "New conversation")


def get_owned_conversation(db: Session, actor: Principal, conversation_id: str) -> Conversation | None:
    """Ownership is part of the query: someone else's conversation ID matches nothing, exactly like a missing one."""
    return db.execute(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == actor.user_code, Conversation.deleted_at.is_(None))).scalar_one_or_none()


def run_chat(db: Session, actor: Principal, message: str, conversation_id: str | None = None, emit: Emit | None = None) -> AgentResult:
    emit = emit or (lambda _e: None)
    provider, model = provider_label()
    trace = TraceRecorder(actor.user_code, conversation_id, provider, model)

    def fail(e: LLMError, conv: Conversation | None, created: bool) -> AgentError:
        audit_service.record(db, "LLM_FAILURE", "llm.call", "FAILURE", actor=actor, reason=e.code, trace_id=trace.trace_id, details={"error": type(e).__name__, "detail": e.detail[:300]})
        trace.step("error", code=e.code)
        trace.finish(db, outcome="llm_error", error_code=e.code)
        if conv is not None and created:
            db.delete(conv)
        db.commit()
        return AgentError(e.code, e.http_status, e.user_message, e.retryable, trace.trace_id, None if created else (conv.id if conv else None))

    redacted, labels = redact_secrets(message)
    if labels:
        trace.step("guardrail", detail="secret(s) masked before processing", labels=labels)
        audit_service.record(db, "SECRET_SHARED", "chat.secret_in_message", "SUCCESS", actor=actor, reason="masked and not stored", trace_id=trace.trace_id, details={"labels": labels})

    conv, created = None, False
    if conversation_id:
        conv = get_owned_conversation(db, actor, conversation_id)
        if conv is None:
            raise AgentError("CONVERSATION_NOT_FOUND", 404, "Conversation not found.", False)
    else:
        conv = Conversation(id=secrets.token_hex(16), user_id=actor.user_code, title=_title(redacted))
        db.add(conv); db.flush(); created = True
    trace.conversation_id = conv.id

    try:
        client = get_client()
    except LLMError as e:
        raise fail(e, conv, created)
    trace.provider, trace.model_name = client.name, client.model

    prior = db.execute(select(ConversationMessage).where(ConversationMessage.conversation_id == conv.id).order_by(ConversationMessage.id.desc()).limit(40)).scalars().all()
    history = [{"role": m.role, "content": m.message_content} for m in reversed(prior)]
    memories = memory_service.select_relevant(db, actor.user_code, redacted, config.MEMORY_MAX_SELECTED)
    trace.memory_refs = [m.id for m in memories]
    messages, ctx_info = context_builder.build_messages(actor, memories, today().isoformat(), history, redacted)
    trace.step("context", **ctx_info)

    tctx = ToolContext(db=db, actor=actor, trace=trace, today=today(), conversation_id=conv.id)
    specs = tool_specs(actor.role)
    final_text, outcome = "", "answered"
    pending_numbers: list[str] = []
    tickets: list[str] = []
    saved_memory: set[tuple[str, str]] = set()

    try:
        for _ in range(max(1, config.AGENT_MAX_ITERATIONS)):
            emit({"type": "status", "stage": "model", "text": "Thinking"})
            resp = client.chat(messages, specs)
            trace.model_call(latency_ms=resp.latency_ms, input_tokens=resp.input_tokens, output_tokens=resp.output_tokens, estimated=resp.estimated, tool_names=[c.name[:60] for c in resp.tool_calls])
            messages.append(resp.assistant_message)
            if not resp.tool_calls:
                final_text = (resp.text or "").strip()
                break
            for tc in resp.tool_calls:
                emit({"type": "status", "stage": "tool", "tool": tc.name[:60], "text": STATUS_TEXT.get(tc.name, "Working")})
                result = _run_one(tctx, tc)
                messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": json.dumps(result, ensure_ascii=False)})
                if not result["success"] and result["error"]["code"] == "FORBIDDEN":
                    outcome = "blocked"
                elif result["success"]:
                    d = result["data"]
                    if d.get("status") == "PENDING_APPROVAL":
                        pending_numbers.append(d["request_number"]); outcome = "pending_approval" if outcome != "blocked" else outcome
                    if tc.name == "create_support_ticket":
                        tickets.append(d["ticket"]["ticket_number"])
                    if tc.name == "save_memory":
                        saved_memory.add((d["key"], d["value"]))
        else:
            outcome = "max_iterations"
            final_text = "I could not finish that request within the allowed number of steps. Please rephrase it or contact the Program Office."
    except LLMError as e:
        raise fail(e, conv, created)

    if not final_text:
        final_text = "I could not produce an answer. Please try again."

    # rule-based safety net for durable preferences (runs on the REDACTED text, so secrets can never be extracted)
    for key, value in memory_service.extract_with_rules(redacted):
        if (key, value) in saved_memory:
            continue
        sp = db.begin_nested()
        try:
            m = memory_service.save(db, actor, key, value, source="rules", trace_id=trace.trace_id)
            sp.commit(); trace.memory_refs.append(m.id); trace.step("memory", detail=f"saved {key}")
        except memory_service.MemoryRejected:
            sp.rollback(); trace.step("memory", detail=f"{key} not stored (rejected)")

    um = ConversationMessage(conversation_id=conv.id, role="user", message_content=redacted, trace_id=trace.trace_id)
    db.add(um); db.flush()
    sources = list({s["id"]: s for s in trace.retrieval}.values())
    approvals = [approval_service.to_dict(approval_service.get(db, actor, n)) for n in pending_numbers]
    am = ConversationMessage(conversation_id=conv.id, role="assistant", message_content=final_text, trace_id=trace.trace_id,
                             meta={"sources": sources, "tools": [{"name": t["name"], "status": t["status"]} for t in trace.tools_used], "approvals": pending_numbers,
                                   "tickets": tickets, "outcome": outcome})
    db.add(am)
    conv.updated_at = utcnow()
    db.flush()
    trace.finish(db, outcome=outcome)
    db.commit()
    return AgentResult(reply=final_text, conversation_id=conv.id, user_message_id=um.id, assistant_message_id=am.id, trace=trace.summary(), sources=sources,
                       approvals=approvals, tickets=tickets, redactions=labels, outcome=outcome, context=ctx_info)


def _run_one(tctx: ToolContext, tc: ToolCall) -> dict[str, Any]:
    if tc.arguments is None:                                   # the model sent arguments that are not a JSON object
        audit_service.record(tctx.db, "TOOL_REJECTED", f"tool.{tc.name[:40]}", "FAILURE", actor=tctx.actor, reason="malformed tool arguments", trace_id=tctx.trace.trace_id)
        tctx.db.commit()
        tctx.trace.tool_call(name=tc.name[:60], args={}, ok=False, latency_ms=0, error_code="INVALID_ARGUMENTS", status="rejected")
        return err("INVALID_ARGUMENTS", "The tool arguments were not valid JSON. Call the tool again with a valid JSON object.")
    return run_tool(tctx, tc.name, tc.arguments)
