"""Restricted tool registry and executor.

The model can only name a tool and propose arguments. Before anything runs, the backend independently checks:
the tool exists, the caller's role may use it, the arguments validate against a strict Pydantic schema, and the
tool itself enforces resource ownership through the authorization service. Each tool runs inside a savepoint so a
failure leaves no partial change. Results always use one envelope: {"success": true, "data": ...} or
{"success": false, "error": {"code", "message"}}. Unexpected exceptions are logged with a correlation ID and
reported to the model/user as a generic error (no internals).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.logging_config import logger
from app.schemas import llm_parameters
from app.security.context import Principal, correlation_id
from app.security.redaction import redact_args
from app.services import audit_service, authorization_service as authz
from app.services.trace_service import TraceRecorder


class ToolError(Exception):
    """An expected, user-presentable failure (not found, invalid business input, ...)."""
    def __init__(self, code: str, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data or {}


@dataclass
class ToolContext:
    db: Session
    actor: Principal
    trace: TraceRecorder
    today: date
    conversation_id: str | None = None


@dataclass
class ToolDef:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], dict[str, Any]]
    roles: tuple[str, ...] = ("student",)
    sensitive: bool = False                    # sensitive tools only ever create approval requests


REGISTRY: dict[str, ToolDef] = {}


def register(name: str, description: str, args_model: type[BaseModel], roles: tuple[str, ...] = ("student",), sensitive: bool = False):
    def deco(fn: Callable[[ToolContext, Any], dict[str, Any]]):
        REGISTRY[name] = ToolDef(name, description, args_model, fn, roles, sensitive)
        return fn
    return deco


def tool_specs(role: str) -> list[dict[str, Any]]:
    """Function-calling specs offered to the model for this role (a student is never even shown staff-only tools)."""
    return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": llm_parameters(t.args_model)}}
            for t in REGISTRY.values() if role in t.roles]


def ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "data": data}


def err(code: str, message: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    e: dict[str, Any] = {"code": code, "message": message}
    if extra:
        e.update(extra)
    return {"success": False, "error": e}


_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.\-]")


def safe_tool_name(name: Any) -> str:
    return _SAFE_NAME.sub("?", str(name))[:60]


def run_tool(ctx: ToolContext, name: str, raw_args: Any) -> dict[str, Any]:
    t0 = time.perf_counter()
    db, actor, trace = ctx.db, ctx.actor, ctx.trace
    shown = safe_tool_name(name)
    args_for_log = redact_args(raw_args if isinstance(raw_args, dict) else {"_raw": str(raw_args)[:200]})

    def finish(result: dict[str, Any], status: str) -> dict[str, Any]:
        db.commit()                                               # persist audit rows even when the tool itself failed
        trace.tool_call(name=shown, args=args_for_log, ok=result["success"], latency_ms=round((time.perf_counter() - t0) * 1000),
                        error_code=None if result["success"] else result["error"]["code"], status=status)
        return result

    tool = REGISTRY.get(name) if isinstance(name, str) else None
    if tool is None:
        audit_service.record(db, "TOOL_REJECTED", "tool.unknown", "FAILURE", actor=actor, reason="unknown tool name", trace_id=trace.trace_id, details={"tool": shown})
        return finish(err("UNKNOWN_TOOL", "That tool does not exist."), "rejected")
    if actor.role not in tool.roles:
        audit_service.record(db, "ACCESS_DENIED", f"tool.{tool.name}", "DENIED", actor=actor, authorization_decision="DENIED", reason=f"role {actor.role} not permitted", trace_id=trace.trace_id)
        return finish(err("FORBIDDEN", authz.FORBIDDEN_MESSAGE), "blocked")
    try:
        if not isinstance(raw_args, dict):                          # strict: never silently turn garbage into "no arguments"
            raise TypeError("arguments must be an object")
        args = tool.args_model(**raw_args)
    except (ValidationError, TypeError) as e:
        problems = [f"{'.'.join(map(str, x['loc'])) or 'arguments'}: {x['msg']}" for x in e.errors()[:5]] if isinstance(e, ValidationError) else ["arguments must be an object"]
        audit_service.record(db, "TOOL_REJECTED", f"tool.{tool.name}", "FAILURE", actor=actor, reason="invalid arguments", trace_id=trace.trace_id, details={"problems": problems})
        return finish(err("INVALID_ARGUMENTS", "The arguments were not valid: " + "; ".join(problems)), "rejected")
    sp = db.begin_nested()
    try:
        data = tool.handler(ctx, args)
        sp.commit()
        status = "pending_approval" if tool.sensitive and data.get("status") == "PENDING_APPROVAL" else "ok"
        return finish(ok(data), status)
    except authz.Forbidden as e:
        sp.rollback()
        authz.record_denied(db, actor, e, trace.trace_id, commit=False)
        return finish(err("FORBIDDEN", authz.FORBIDDEN_MESSAGE), "blocked")
    except ToolError as e:
        sp.rollback()
        return finish(err(e.code, e.message, e.data), "error")
    except Exception as e:                                          # noqa: BLE001 - deliberate catch-all, reported safely
        sp.rollback()
        logger.exception("tool %s failed (correlation %s)", shown, correlation_id())
        try:
            audit_service.record(db, "TOOL_FAILURE", f"tool.{tool.name}", "FAILURE", actor=actor, reason=f"unexpected {type(e).__name__}", trace_id=trace.trace_id)
        except Exception:                                           # noqa: BLE001  the user still gets a clean error even if the audit write itself fails
            logger.exception("could not audit a tool failure (correlation %s)", correlation_id())
            db.rollback()
        return finish(err("TOOL_FAILED", "The tool could not be completed. Please try again or contact the Program Office.", {"reference": correlation_id()}), "error")
