import re

import pytest
from fastapi.testclient import TestClient

from app import config


def prod(monkeypatch, **over):
    base = dict(ENV="production", SECRET_KEY="k" * 40, SESSION_COOKIE_SECURE=True, SHOW_DEMO_ACCOUNTS=False, LLM_PROVIDER="groq")
    base.update(over)
    for k, v in base.items():
        monkeypatch.setattr(config, k, v)


def test_a_correct_production_configuration_has_no_problems(monkeypatch):
    prod(monkeypatch)
    assert config.validate_startup() == []


@pytest.mark.parametrize("over,needle", [({"SECRET_KEY": ""}, "SECRET_KEY"), ({"SECRET_KEY": "short"}, "SECRET_KEY"), ({"SECRET_KEY": "replace-with-a-random-secret" + "x" * 20}, "SECRET_KEY"),
                                         ({"SESSION_COOKIE_SECURE": False}, "SESSION_COOKIE_SECURE"), ({"SHOW_DEMO_ACCOUNTS": True}, "SHOW_DEMO_ACCOUNTS"), ({"LLM_PROVIDER": "mock"}, "mock")])
def test_insecure_production_settings_are_refused(monkeypatch, over, needle):
    prod(monkeypatch, **over)
    assert any(needle in p for p in config.validate_startup())


def test_the_app_refuses_to_start_in_a_misconfigured_production(database, monkeypatch):
    prod(monkeypatch, SECRET_KEY="short")
    from app.main import create_app
    with pytest.raises(RuntimeError, match="Refusing to start"):
        with TestClient(create_app()):
            pass


def test_missing_llm_key_does_not_stop_the_application_from_starting(database, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq"); monkeypatch.setattr(config, "GROQ_API_KEY", ""); monkeypatch.setattr(config, "GROQ_MODEL", "")
    assert config.validate_startup() == []                                           # login, authorization and approvals must work without the model
    from app.main import create_app
    with TestClient(create_app()) as c:
        assert c.get("/api/health").json()["llm"]["configured"] is False


def test_llm_configuration_states(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    for key, model, ok in (("", "", False), ("k", "", False), ("", "m", False), ("k", "m", True)):
        monkeypatch.setattr(config, "GROQ_API_KEY", key); monkeypatch.setattr(config, "GROQ_MODEL", model)
        assert config.llm_configured()[0] is ok
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    assert config.llm_configured()[0] is False and "unsupported" in config.llm_configured()[1]


def test_no_model_name_is_hard_coded_and_the_default_provider_is_groq():
    assert config.GROQ_BASE_URL == "https://api.groq.com/openai/v1" or "GROQ_BASE_URL" in __import__("os").environ
    src = (config.ROOT / "app" / "config.py").read_text()
    assert 'GROQ_MODEL = _env("GROQ_MODEL", "")' in src and 'GROQ_API_KEY = _env("GROQ_API_KEY", "")' in src and 'LLM_PROVIDER = _env("LLM_PROVIDER", "groq")' in src
    assert "XAI" not in src and "xAI" not in src


def test_no_secret_shaped_strings_are_committed():
    pat = re.compile(r"gsk_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{20,}|xai-[A-Za-z0-9]{20,}|grok_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}")
    for p in list((config.ROOT / "app").rglob("*.py")) + list((config.ROOT / "static").glob("*")) + [config.ROOT / ".env.example"]:
        if p.exists():
            assert not pat.search(p.read_text(encoding="utf-8", errors="ignore")), p


def test_demo_passwords_exist_only_in_the_dev_only_seed_module():
    from app.services.seed_service import DEMO_PASSWORDS
    for p in (config.ROOT / "app").rglob("*.py"):
        if p.name == "seed_service.py":
            continue
        txt = p.read_text(encoding="utf-8")
        assert not any(pw in txt for pw in DEMO_PASSWORDS.values()), p


def test_env_example_documents_every_setting_the_code_reads_and_has_no_real_secrets():
    src = (config.ROOT / "app" / "config.py").read_text(encoding="utf-8")
    names = set(re.findall(r'_(?:env|int|float|bool)\("([A-Z_]+)"', src))
    example = (config.ROOT / ".env.example").read_text(encoding="utf-8")
    missing = [n for n in sorted(names) if not re.search(rf"^#?\s*{n}=", example, flags=re.M)]
    assert missing == [], f"add to .env.example: {missing}"
    assert "your-groq-api-key" in example and "XAI_" not in example and not re.search(r"xai-[A-Za-z0-9]{20,}|gsk_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9]{20,}", example)
    assert re.search(r"^LLM_PROVIDER=groq$", example, flags=re.M)             # the template's default is a real provider, never the mock


def test_requirements_are_pinned():
    for f in ("requirements.txt", "requirements-dev.txt"):
        lines = [l for l in (config.ROOT / f).read_text().splitlines() if l.strip() and not l.startswith(("#", "-r"))]
        assert lines and all("==" in l for l in lines), f
    reqs = (config.ROOT / "requirements.txt").read_text().lower()
    for pkg in ("fastapi", "sqlalchemy", "alembic", "pydantic", "werkzeug", "openai", "python-dotenv", "uvicorn"):
        assert pkg + "==" in reqs


def test_relative_sqlite_paths_resolve_against_the_project_root_not_the_cwd():
    n = config.normalize_database_url
    assert n("sqlite:///instance/x.db") == f"sqlite:///{config.ROOT / 'instance' / 'x.db'}"
    assert n("sqlite:////abs/path/x.db") == "sqlite:////abs/path/x.db" and n("sqlite://") == "sqlite://" and n("sqlite:///:memory:") == "sqlite:///:memory:"
    assert n("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_grok_style_keys_are_masked_everywhere_secrets_are_masked():
    from app.security.redaction import redact_secrets
    key = "grok_" + "AbC123xyz987-_" * 7
    for text in (f"my key is {key}", f"Authorization failed for {key} on api", f"remember that the key is {key}"):
        out, labels = redact_secrets(text)
        assert key not in out and "api_key" in labels or "secret_value" in labels, text
    assert "xai-" + "A1b2C3d4" * 4 not in redact_secrets("xai-" + "A1b2C3d4" * 4)[0]
