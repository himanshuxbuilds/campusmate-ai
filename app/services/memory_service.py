"""Persistent, student-scoped memory.

Stored: durable, non-sensitive facts in explicit categories (preference, profile, support_context, interest, plan).
Never stored: passwords, OTPs, tokens, API keys, card/ID numbers, temporary remarks, raw chat. Each value is validated
(length, single line, no secret patterns, no credential words). Selection returns only items relevant to the current
question, never the whole table.
"""
from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Memory
from app.security.context import Principal
from app.security.redaction import memory_value_is_safe
from app.services import audit_service
from app.services.retrieval_service import tokens

KEY_CATEGORY = {"response_style": "preference", "preferred_language": "preference", "preferred_name": "preference", "contact_preference": "preference",
                "programme": "profile", "interest": "interest", "plan": "plan", "hostel_block": "support_context", "support_topic": "support_context"}
SINGLE_VALUED = {"response_style", "preferred_language", "preferred_name", "contact_preference", "programme", "hostel_block"}
ALWAYS_FOR_ANSWERS = {"response_style", "preferred_language"}                 # shape every substantive answer
MAX_ITEMS = 25
DEFAULT_TTL_DAYS = {"support_context": 90, "plan": 180}
_TEMPORARY = re.compile(r"\b(right now|today|tonight|this morning|this evening|at the moment|currently in|for now)\b", re.I)
_GREETING = {"hi", "hello", "hey", "thanks", "thank", "ok", "okay"}
_CONTACT_WORDS = {"contact", "notify", "email", "whatsapp", "sms", "call", "reach", "update"}


class MemoryRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_RULES: list[tuple[str, re.Pattern]] = [
    ("preferred_name", re.compile(r"\b(?:call me|my name is|i go by)\s+([A-Z][a-zA-Z]{1,20})\b", re.I)),
    ("preferred_language", re.compile(r"\b(?:reply|answer|talk|respond)\s+(?:to me\s+)?in\s+(hindi|gujarati|english|marathi)\b", re.I)),
    ("preferred_language", re.compile(r"\bi prefer (hindi|gujarati|english|marathi)\b", re.I)),
    ("contact_preference", re.compile(r"\b(?:contact|reach|notify|update)\s+me\s+(?:on|via|by|through)\s+(email|whatsapp|sms|phone|call)\b", re.I)),
    ("programme", re.compile(r"\bi(?:'m| am) enrolled in (?:the )?(.{3,60}?)\s+programme\b", re.I)),
    ("plan", re.compile(r"\bi (?:am planning|plan|want|intend) to (apply for [^.,;]{3,60}|opt out of placements|take [^.,;]{3,60}|switch to [^.,;]{3,60})", re.I)),
    ("interest", re.compile(r"\bi(?:'m| am) (?:interested in|passionate about|focusing on) ([^.,;]{3,50})", re.I)),
    ("hostel_block", re.compile(r"\bi (?:live|stay) in (hostel block [a-z]\b)", re.I)),
]
_STYLE = re.compile(r"\b(?:keep|make|give me|i prefer|please use|please keep|prefer)\b[^.\n]{0,60}?\b(concise|brief|short|detailed|simple|step[- ]by[- ]step)\b", re.I)


def extract_with_rules(text: str) -> list[tuple[str, str]]:
    """Rule-based safety net run on the REDACTED message (so secrets can never be extracted)."""
    out: list[tuple[str, str]] = []
    for key, pat in _RULES:
        for m in pat.finditer(text):
            v = m.group(1).strip()
            out.append((key, f"{v} programme" if key == "programme" else v))
    m = _STYLE.search(text)
    if m:
        style = m.group(1).lower().replace("-", " ")
        out.append(("response_style", f"{style} academic explanations" if "academic" in text.lower() else f"{style} explanations"))
    return out


def save(db: Session, actor: Principal, key: str, value: str, *, source: str = "chat", trace_id: str | None = None) -> Memory:
    if key not in KEY_CATEGORY:
        raise MemoryRejected("unsupported memory key")
    value = " ".join((value or "").split()).rstrip(".")
    if not memory_value_is_safe(value):
        raise MemoryRejected("value looks sensitive or invalid; sensitive information is never stored")
    if _TEMPORARY.search(value):
        raise MemoryRejected("temporary statements are not stored")
    category = KEY_CATEGORY[key]
    if key in SINGLE_VALUED:
        db.execute(delete(Memory).where(Memory.user_id == actor.user_code, Memory.memory_key == key))
    else:
        db.execute(delete(Memory).where(Memory.user_id == actor.user_code, Memory.memory_key == key, Memory.memory_value == value))
    ttl = DEFAULT_TTL_DAYS.get(category)
    m = Memory(user_id=actor.user_code, category=category, memory_key=key, memory_value=value, source=source,
               expires_at=utcnow() + timedelta(days=ttl) if ttl else None)
    db.add(m)
    db.flush()
    rows = db.execute(select(Memory).where(Memory.user_id == actor.user_code).order_by(Memory.id)).scalars().all()
    for old in rows[:-MAX_ITEMS]:
        db.delete(old)
    audit_service.record(db, "MEMORY_CREATED", "memory.save", "SUCCESS", actor=actor, target_type="memory", target_id=str(m.id),
                         reason=f"{category}/{key}", trace_id=trace_id, details={"key": key, "category": category})   # value deliberately not logged
    return m


def list_all(db: Session, user_code: str) -> list[Memory]:
    purge_expired(db)
    return list(db.execute(select(Memory).where(Memory.user_id == user_code).order_by(Memory.id)).scalars())


def delete_item(db: Session, actor: Principal, memory_id: int) -> bool:
    """Ownership enforced in the query itself: another user's ID simply matches nothing."""
    m = db.execute(select(Memory).where(Memory.id == memory_id, Memory.user_id == actor.user_code)).scalar_one_or_none()
    if m is None:
        return False
    audit_service.record(db, "MEMORY_DELETED", "memory.delete", "SUCCESS", actor=actor, target_type="memory", target_id=str(m.id),
                         reason=f"{m.category}/{m.memory_key}", details={"key": m.memory_key})
    db.delete(m)
    db.flush()
    return True


def delete_by_key(db: Session, actor: Principal, key: str) -> int:
    rows = db.execute(select(Memory).where(Memory.user_id == actor.user_code, Memory.memory_key == key)).scalars().all()
    for m in rows:
        delete_item(db, actor, m.id)
    return len(rows)


def purge_expired(db: Session) -> int:
    res = db.execute(delete(Memory).where(Memory.expires_at.is_not(None), Memory.expires_at < utcnow()))
    return res.rowcount or 0


def select_relevant(db: Session, user_code: str, query: str, limit: int) -> list[Memory]:
    """Only memory that matters for THIS question: answer-shaping preferences for substantive questions, the preferred name for
    greetings, contact preference for contact questions, and any other item whose words overlap the question."""
    q = set(tokens(query))
    raw = set(re.findall(r"[a-z]+", (query or "").lower()))
    items = list_all(db, user_code)
    if not items:
        return []
    greeting = bool(raw) and raw <= (_GREETING | {"there"})
    chosen: list[Memory] = []
    for m in items:
        words = set(tokens(f"{m.memory_key} {m.memory_value}"))
        if greeting:
            take = m.memory_key == "preferred_name"
        elif m.memory_key in ALWAYS_FOR_ANSWERS:
            take = True
        elif m.memory_key == "contact_preference":
            take = bool(raw & _CONTACT_WORDS)
        else:
            take = bool(q & words)
        if take:
            chosen.append(m)
    return chosen[:limit]
