"""Agent execution trace: one record per chat request with every model call and tool call.

Token counts carry an explicit `estimated` flag: True means a local estimate (e.g. the offline test
double), False means reported by the provider. The two are never mixed silently.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import iso, utcnow
from app.models import AgentTrace
from app.security.context import correlation_id, new_id


class TraceRecorder:
    def __init__(self, user_id: str, conversation_id: str | None, provider: str, model_name: str) -> None:
        self.trace_id = new_id("TR-")
        self.user_id, self.conversation_id, self.provider, self.model_name = user_id, conversation_id, provider, model_name
        self.t0 = time.perf_counter()
        self.steps: list[dict[str, Any]] = []
        self.tools_used: list[dict[str, Any]] = []
        self.retrieval: list[dict[str, Any]] = []
        self.memory_refs: list[int] = []
        self.iterations = 0
        self.in_tok = self.out_tok = 0
        self.estimated = False
        self.model_ms = self.tool_ms = 0
        self.outcome, self.error_code = "answered", None

    def _t(self) -> int:
        return round((time.perf_counter() - self.t0) * 1000)

    def step(self, kind: str, **data: Any) -> None:
        self.steps.append({"n": len(self.steps) + 1, "t_ms": self._t(), "kind": kind, **data})

    def model_call(self, *, latency_ms: int, input_tokens: int, output_tokens: int, estimated: bool, tool_names: list[str]) -> None:
        self.iterations += 1
        self.model_ms += latency_ms
        self.in_tok += input_tokens or 0
        self.out_tok += output_tokens or 0
        self.estimated = self.estimated or estimated
        self.step("model", iteration=self.iterations, latency_ms=latency_ms, input_tokens=input_tokens, output_tokens=output_tokens,
                  tokens_estimated=estimated, decision="tool_call" if tool_names else "final_answer", tools=tool_names)

    def tool_call(self, *, name: str, args: dict[str, Any], ok: bool, latency_ms: int, error_code: str | None, status: str) -> None:
        self.tool_ms += latency_ms
        item = {"name": name, "arguments": args, "ok": ok, "latency_ms": latency_ms, "error_code": error_code, "status": status}
        self.tools_used.append(item)
        self.step("tool", **item)

    def finish(self, db: Session, outcome: str | None = None, error_code: str | None = None) -> AgentTrace:
        self.outcome = outcome or self.outcome
        self.error_code = error_code or self.error_code
        row = AgentTrace(trace_id=self.trace_id, conversation_id=self.conversation_id, user_id=self.user_id, provider=self.provider,
                         model_name=self.model_name, tools_used=self.tools_used, retrieval_references=self.retrieval,
                         memory_references=self.memory_refs, steps=self.steps, iterations=self.iterations, input_tokens=self.in_tok,
                         output_tokens=self.out_tok, tokens_estimated=self.estimated, latency_ms=self._t(), model_latency_ms=self.model_ms,
                         tool_latency_ms=self.tool_ms, outcome=self.outcome, error_code=self.error_code, correlation_id=correlation_id())
        db.add(row)
        db.flush()
        return row

    def summary(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "provider": self.provider, "model": self.model_name, "iterations": self.iterations,
                "latency_ms": self._t(), "model_latency_ms": self.model_ms, "tool_latency_ms": self.tool_ms,
                "input_tokens": self.in_tok, "output_tokens": self.out_tok,
                "token_source": "estimated (local)" if self.estimated else "reported by provider",
                "tools": self.tools_used, "retrieval": self.retrieval, "steps": self.steps, "outcome": self.outcome}


def to_dict(t: AgentTrace) -> dict[str, Any]:
    return {"trace_id": t.trace_id, "timestamp": iso(t.created_at), "conversation_id": t.conversation_id, "user_id": t.user_id, "provider": t.provider,
            "model": t.model_name, "iterations": t.iterations, "input_tokens": t.input_tokens, "output_tokens": t.output_tokens,
            "token_source": "estimated (local)" if t.tokens_estimated else "reported by provider", "latency_ms": t.latency_ms,
            "model_latency_ms": t.model_latency_ms, "tool_latency_ms": t.tool_latency_ms, "outcome": t.outcome, "error_code": t.error_code,
            "tools_used": t.tools_used, "retrieval_references": t.retrieval_references, "memory_references": t.memory_references,
            "correlation_id": t.correlation_id}


def query(db: Session, *, user_id: str | None = None, outcome: str | None = None, limit: int = 50, offset: int = 0) -> list[AgentTrace]:
    stmt = select(AgentTrace)
    if user_id: stmt = stmt.where(AgentTrace.user_id == user_id)
    if outcome: stmt = stmt.where(AgentTrace.outcome == outcome)
    return list(db.execute(stmt.order_by(AgentTrace.id.desc()).limit(max(1, min(limit, 200))).offset(max(0, offset))).scalars())
