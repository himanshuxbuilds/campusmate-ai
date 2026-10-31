"""Central configuration. Every value comes from environment variables (.env in development).

Code must read settings as `config.NAME` at call time (never `from app.config import NAME`)
so tests can override them with monkeypatch.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).lower() in ("1", "true", "yes", "on")


APP_NAME = "CampusMate AI"
APP_VERSION = "3.0.0"
INSTITUTE = "Adani Institute of Digital Technology Management (AIDTM)"
ENV = _env("APP_ENV", _env("FLASK_ENV", "development")).lower()          # development | production | test
IS_DEV = ENV in ("development", "test")

# --- secrets / database -------------------------------------------------------------
SECRET_KEY = _env("SECRET_KEY", "")                                      # required outside development
def normalize_database_url(url: str) -> str:
    """A relative SQLite path (e.g. sqlite:///instance/campus_desk.db) is resolved against the PROJECT ROOT, not the current directory, so
    every script and the server always open the same file wherever they are started from."""
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith(prefix + "/") and ":memory:" not in url:
        path = url[len(prefix):]
        if path and not Path(path).is_absolute():
            return prefix + str(ROOT / path)
    return url


DATABASE_URL = normalize_database_url(_env("DATABASE_URL", f"sqlite:///{ROOT / 'instance' / 'campus_desk.db'}"))

# --- LLM: Groq Cloud (OpenAI-compatible API with native function calling); "mock" is an explicit offline test double ---
LLM_PROVIDER = _env("LLM_PROVIDER", "groq").lower()                      # groq | mock
GROQ_API_KEY = _env("GROQ_API_KEY", "")
GROQ_MODEL = _env("GROQ_MODEL", "")                                      # no default on purpose: pick a tool-calling model from https://console.groq.com/docs/models
GROQ_BASE_URL = _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
LLM_TEMPERATURE = None if _env("LLM_TEMPERATURE", "0.1").lower() in ("", "none", "off", "omit") else _float("LLM_TEMPERATURE", 0.1)   # None = do not send the parameter
LLM_MAX_RETRIES = _int("LLM_MAX_RETRIES", 2)
AGENT_MAX_ITERATIONS = _int("AGENT_MAX_ITERATIONS", 5)
AGENT_TIMEOUT_SECONDS = _int("AGENT_TIMEOUT_SECONDS", 60)

# --- context selection ---------------------------------------------------------------
CONTEXT_TOKEN_BUDGET = _int("CONTEXT_TOKEN_BUDGET", 6000)                # approx tokens for system+memory+history
HISTORY_MAX_TURNS = _int("HISTORY_MAX_TURNS", 8)
MAX_MESSAGE_CHARS = _int("MAX_MESSAGE_CHARS", 2000)
MEMORY_MAX_SELECTED = _int("MEMORY_MAX_SELECTED", 5)

# --- retrieval / policy document ----------------------------------------------------------
POLICY_DOCUMENT_PATH = _env("POLICY_DOCUMENT_PATH", "data/kb/AIDTM_Student_Handbook_KB_v3.md")
POLICY_DOCUMENT_NAME = _env("POLICY_DOCUMENT_NAME", "AIDTM Student Handbook KB")
POLICY_DOCUMENT_VERSION = _env("POLICY_DOCUMENT_VERSION", "3.0")
CHUNK_SIZE = _int("CHUNK_SIZE", 900)                                     # words, used for unstructured documents
CHUNK_OVERLAP = _int("CHUNK_OVERLAP", 120)
RETRIEVAL_TOP_K = _int("RETRIEVAL_TOP_K", 5)
RETRIEVAL_MIN_SCORE = _float("RETRIEVAL_MIN_SCORE", 2.0)
RETRIEVAL_OOV_RATIO = _float("RETRIEVAL_OOV_RATIO", 0.4)          # abstain when this share of the question weight is on words absent from the handbook
CHUNK_CONTEXT_CHARS = _int("CHUNK_CONTEXT_CHARS", 2500)

# --- sessions / auth ------------------------------------------------------------------
SESSION_COOKIE_NAME = _env("SESSION_COOKIE_NAME", "campus_session")
SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", not IS_DEV)
SESSION_ABSOLUTE_MINUTES = _int("SESSION_ABSOLUTE_MINUTES", 480)
SESSION_IDLE_MINUTES = _int("SESSION_IDLE_MINUTES", 30)
PASSWORD_HASH_METHOD = _env("PASSWORD_HASH_METHOD", "scrypt")
PASSWORD_MIN_LENGTH = _int("PASSWORD_MIN_LENGTH", 8)
LOGIN_MAX_FAILURES = _int("LOGIN_MAX_FAILURES", 5)                       # per account, within the window
LOGIN_MAX_FAILURES_PER_IP = _int("LOGIN_MAX_FAILURES_PER_IP", 25)
LOGIN_LOCKOUT_MINUTES = _int("LOGIN_LOCKOUT_MINUTES", 15)
CHAT_RATE_LIMIT_PER_MIN = _int("CHAT_RATE_LIMIT_PER_MIN", 20)
API_RATE_LIMIT_PER_MIN = _int("API_RATE_LIMIT_PER_MIN", 300)
SHOW_DEMO_ACCOUNTS = _bool("SHOW_DEMO_ACCOUNTS", IS_DEV)                 # login-page hint; development only
ALLOWED_ORIGINS = [o for o in _env("ALLOWED_ORIGINS", "").split(",") if o]
ENABLE_API_DOCS = _bool("ENABLE_API_DOCS", IS_DEV)

# --- approvals / retention ---------------------------------------------------------------
APPROVAL_TTL_HOURS = _int("APPROVAL_TTL_HOURS", 72)
TICKET_DUPLICATE_WINDOW_MINUTES = _int("TICKET_DUPLICATE_WINDOW_MINUTES", 10)
RETENTION_MESSAGES_DAYS = _int("RETENTION_MESSAGES_DAYS", 90)
RETENTION_TRACES_DAYS = _int("RETENTION_TRACES_DAYS", 90)
RETENTION_AUDIT_DAYS = _int("RETENTION_AUDIT_DAYS", 365)
RETENTION_LOGIN_ATTEMPTS_DAYS = _int("RETENTION_LOGIN_ATTEMPTS_DAYS", 30)

# "Today" for date arithmetic in demos/evaluation (fee late-fee maths, "next exam"). Empty = real clock.
DEMO_TODAY = _env("DEMO_TODAY", "")

# --- paths --------------------------------------------------------------------------------
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
EVAL_RESULTS_DIR = ROOT / "eval" / "results"
CURRICULUM_FILE = DATA_DIR / "curriculum.json"
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()


def provider_settings() -> tuple[str, str, str, str]:
    """(label, api_key, model, base_url) of the selected provider; ('', '', '', '') for mock/unsupported."""
    if LLM_PROVIDER == "groq":
        return "Groq", GROQ_API_KEY, GROQ_MODEL, GROQ_BASE_URL
    return "", "", "", ""


def llm_configured() -> tuple[bool, str]:
    """(ok, reason). Used by /api/health and the chat route; never raises."""
    if LLM_PROVIDER == "mock":
        return True, "mock provider (offline test double)"
    if LLM_PROVIDER != "groq":
        return False, f"unsupported LLM_PROVIDER '{LLM_PROVIDER}' (use 'groq' or 'mock')"
    label, key, model, _ = provider_settings()
    if not key:
        return False, "GROQ_API_KEY is not set"
    if not model:
        return False, "GROQ_MODEL is not set"
    return True, f"{label} model '{model}'"


def validate_startup() -> list[str]:
    """Problems that must stop a PRODUCTION start. The LLM key is deliberately not one of them:
    login, authorization and approvals must work without the model."""
    problems = []
    if ENV == "production":
        if len(SECRET_KEY) < 32 or SECRET_KEY.startswith("replace"):
            problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
        if not SESSION_COOKIE_SECURE:
            problems.append("SESSION_COOKIE_SECURE must be true in production (serve over HTTPS)")
        if SHOW_DEMO_ACCOUNTS:
            problems.append("SHOW_DEMO_ACCOUNTS must be false in production")
        if LLM_PROVIDER == "mock":
            problems.append("LLM_PROVIDER=mock is not allowed in production")
    return problems
