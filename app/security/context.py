"""Request-scoped context (correlation ID) and the authenticated principal."""
from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_id(prefix: str, n: int = 10) -> str:
    return f"{prefix}{uuid.uuid4().hex[:n].upper()}"


def correlation_id() -> str:
    cid = correlation_id_var.get()
    if not cid:
        cid = new_id("REQ-", 8)
        correlation_id_var.set(cid)
    return cid


@dataclass(frozen=True)
class Principal:
    """The authenticated identity. Built only from the server-side session, never from request input."""
    user_code: str
    name: str
    role: str                                   # student | staff | admin
    programme: str | None = None
    staff_scope: dict[str, Any] = field(default_factory=dict)
    session_id: int | None = None

    @property
    def is_student(self) -> bool: return self.role == "student"
    @property
    def is_staff(self) -> bool: return self.role in ("staff", "admin")
    @property
    def is_admin(self) -> bool: return self.role == "admin"
