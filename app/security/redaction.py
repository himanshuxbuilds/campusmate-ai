"""Secret and PII redaction. Runs BEFORE anything is stored, logged, traced or sent to the model."""
from __future__ import annotations

import re
from typing import Any

_I = re.IGNORECASE
# (label, compiled pattern, group index holding the secret value or 0 for the whole match)
_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"), 0),
    ("url_credentials", re.compile(r"://[^\s:/@]+:([^\s@/]+)@"), 1),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{2,}\b"), 0),
    ("api_key", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{8,}|\bxai-[A-Za-z0-9_-]{8,}|\bgrok_[A-Za-z0-9_-]{8,}|\bgsk_[A-Za-z0-9]{8,}|\bAKIA[0-9A-Z]{12,}"
                           r"|\bgh[pousr]_[A-Za-z0-9]{20,}|\bAIza[0-9A-Za-z_-]{20,}"), 0),
    ("bearer_token", re.compile(r"\bbearer\s+([A-Za-z0-9._~+/=-]{10,})", _I), 1),
    ("card_number", re.compile(r"\b(?:\d[ -]?){13,19}\b"), 0),
    ("aadhaar", re.compile(r"\baadhaa?r(?:\s*(?:number|no\.?|card))?\s*(?:is|was|:|=|-)?\s*([0-9][0-9 -]{10,15}[0-9])", _I), 1),
    ("otp", re.compile(r"\b(?:otp|one[- ]time[- ]pass(?:word|code)|verification code)\b[^0-9\n]{0,25}(\d{4,8})\b", _I), 1),
    ("cvv", re.compile(r"\bcvv2?\b\s*(?:is|:|=)?\s*(\d{3,4})\b", _I), 1),
    ("password", re.compile(r"\b(?:password|passwd|pwd|passcode|pin(?:\s+(?:code|number))?)\s*(?:is|was|:|=|-)?\s*(\S{3,80})", _I), 1),
    ("secret_value", re.compile(r"\b(?:secret|token|api[ _-]?key|apikey|access[ _-]?key|private[ _-]?key|credential)s?\b\s*(?:is|was|:|=|-)?\s*(\S{4,200})", _I), 1),
    ("opaque_secret", re.compile(r"(?<![/\w.-])(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}(?![/\w.-])"), 0),
]
_PII_KEYS = {"mobile", "phone", "email", "address", "account_last4", "refund_bank_account_masked", "bank_name", "new_value", "password"}


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """Return (text with secrets masked, sorted labels that were found)."""
    found: set[str] = set()
    out = text or ""
    for label, pat, grp in _PATTERNS:
        def repl(m: re.Match, label=label, grp=grp) -> str:
            if grp and m.lastindex and (m.group(grp) or "").startswith("[REDACTED"):
                return m.group(0)                         # already masked by an earlier, more specific pattern
            found.add(label)
            if grp and m.lastindex:
                s, e = m.span(grp)
                base = m.start()
                return m.group(0)[: s - base] + f"[REDACTED {label.upper()}]" + m.group(0)[e - base:]
            return f"[REDACTED {label.upper()}]"
        out = pat.sub(repl, out)
    return out, sorted(found)


def contains_secret(text: str) -> bool:
    return bool(redact_secrets(text)[1])


_BANNED_MEMORY_WORDS = re.compile(r"\b(password|passwd|otp|pin|cvv|secret|token|api[ _-]?key|credential|credit card|debit card|aadhaar|aadhar|passport)\b", _I)


def memory_value_is_safe(value: str, max_len: int = 200) -> bool:
    """A long-term memory value must be short, single-line, secret-free and free of credential words/long digit runs."""
    v = (value or "").strip()
    if not v or len(v) > max_len or "\n" in v or "[REDACTED" in v or "<" in v or ">" in v:     # no tags: a value must not be able to close the prompt's <memory> block
        return False
    if contains_secret(v) or _BANNED_MEMORY_WORDS.search(v) or re.search(r"\d{6,}", v):
        return False
    return True


def mask_value(key: str, value: Any) -> str:
    s = str(value)
    k = key.lower()
    if k in ("mobile", "phone"):
        return "******" + s[-3:] if len(s) > 3 else "***"
    if k == "email" and "@" in s:
        return s[0] + "***@" + s.split("@", 1)[1]
    if k in ("address", "bank_name", "new_value"):
        return "[redacted]"
    if k in _PII_KEYS:
        return "****"
    return redact_secrets(s)[0][:120]


def redact_args(args: Any, depth: int = 0) -> Any:
    """Copy of tool arguments that is safe to write to traces/logs (PII masked, secrets removed)."""
    if depth > 4:
        return "[nested]"
    if isinstance(args, dict):
        return {k: (mask_value(k, v) if k.lower() in _PII_KEYS and not isinstance(v, (dict, list)) else redact_args(v, depth + 1)) for k, v in args.items()}
    if isinstance(args, list):
        return [redact_args(v, depth + 1) for v in args[:20]]
    if isinstance(args, str):
        return redact_secrets(args)[0][:300]
    return args
