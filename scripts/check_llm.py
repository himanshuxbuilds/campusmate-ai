"""Step-by-step diagnosis of the LLM connection (Groq by default).   python scripts/check_llm.py
Steps 0a-0c talk to the provider directly (raw HTTP) so you see its exact reply; steps 1-3 use the application's own client, tool schemas included.
Isolates WHICH part fails: key, model name, a parameter, native function calling, or the application's tool schemas. The API key is never printed.
This is a LIVE check: it needs GROQ_API_KEY and GROQ_MODEL and makes a few real (cheap) requests."""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config
from app.security.redaction import redact_secrets
from app.services import llm_client

TRIVIAL_TOOL = [{"type": "function", "function": {"name": "get_time", "description": "Returns the current time.", "parameters": {"type": "object", "properties": {}}}}]
PLAIN = [{"role": "user", "content": "Reply with the single word: ok"}]


def _mask(text: str, key: str) -> str:
    return redact_secrets(" ".join((text or "").replace(key, "[KEY]").split()))[0][:500]


def _raw(method: str, base: str, key: str, path: str, body: dict | None = None) -> tuple[int, str]:
    """Plain HTTP with the standard library only (no extra packages). Returns (status, body text) for ANY status; raises URLError if unreachable."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "campus-desk-check/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=config.AGENT_TIMEOUT_SECONDS) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:                                  # 4xx/5xx: the body is exactly what we want to see
        return e.code, e.read().decode("utf-8", "replace")


def run_checks() -> list[tuple[str, bool, str]]:
    ok, why = config.llm_configured()
    out: list[tuple[str, bool, str]] = [("configuration", ok, why)]
    if not ok or config.LLM_PROVIDER == "mock":
        return out
    label, key, model, base = config.provider_settings()

    # 0a: which models can this key use, and is the configured one among them?
    try:
        status, text = _raw("GET", base, key, "/models")
        ids = []
        try:
            ids = sorted(m["id"] for m in json.loads(text).get("data", []) if isinstance(m, dict) and "id" in m)
        except (ValueError, AttributeError):
            pass
        if status != 200 and llm_client._KEY_PROBLEM.search(text or "") or status in (401, 403):
            out.append(("0a model list (raw)", False, f"HTTP {status}: {_mask(text, key)}"))
            out.append(("hint", False, "the provider does not accept this API key. Check GROQ_API_KEY in .env: copy the whole key (shown only once at creation) from console.groq.com > API Keys, "
                                      "with no spaces or quotes (Groq keys usually start with gsk_). A key already set in the Windows environment variables overrides .env."))
            return out                                                   # nothing else can work until the key is fixed
        if status != 200:
            out.append(("0a model list (raw)", status in (404, 405), f"HTTP {status}: {_mask(text, key)}"))
        elif ids and model not in ids:
            out.append(("0a model list (raw)", False, f"model '{model}' is NOT in the list this key can use. Available: {', '.join(ids[:40])}"))
        else:
            out.append(("0a model list (raw)", True, f"HTTP 200; '{model}' is available" + (f"; {len(ids)} models: {', '.join(ids[:25])}" if ids else "")))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        out.append(("0a model list (raw)", False, f"could not reach {base}: {type(e).__name__}"))
        return out

    # 0b / 0c: the simplest possible request, then the same with the temperature parameter the application sends
    results = {}
    for name, extra in (("0b minimal request (raw)", {}), ("0c with temperature (raw)", {"temperature": config.LLM_TEMPERATURE} if config.LLM_TEMPERATURE is not None else None)):
        if extra is None:
            out.append((name, True, "skipped: LLM_TEMPERATURE=none, the parameter is not sent"))
            continue
        try:
            status, text = _raw("POST", base, key, "/chat/completions", {"model": model, "messages": PLAIN, **extra})
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            out.append((name, False, f"could not reach {base}: {type(e).__name__}")); continue
        results[name] = status == 200
        out.append((name, status == 200, f"HTTP {status}: {_mask(text, key)}"))
    if results.get("0b minimal request (raw)") and results.get("0c with temperature (raw)") is False:
        out.append(("hint", False, "the model accepts a request WITHOUT temperature but rejects it WITH: set LLM_TEMPERATURE=none in .env"))

    # 1-3: through the application's own client
    llm_client.reset_client()
    c = llm_client.get_client()

    def step(name, messages, tools):
        t0 = time.perf_counter()
        try:
            r = c.chat(messages, tools)
        except llm_client.LLMError as e:
            out.append((name, False, f"{e.code}: {e.detail}"))
            return
        called = [t.name for t in r.tool_calls]
        out.append((name, True, f"{round((time.perf_counter() - t0) * 1000)} ms, tokens {r.input_tokens}+{r.output_tokens} ({'estimated' if r.estimated else 'provider-reported'}), "
                                f"{'tool call: ' + ', '.join(called) if called else 'text: ' + repr((r.text or '')[:60])}"))

    step("1 plain chat (the application's client)", PLAIN, None)
    step("2 one trivial tool (tests function calling)", [{"role": "user", "content": "What time is it? Use the tool."}], TRIVIAL_TOOL)
    from app.tools import tool_specs
    step("3 full tool set (the application's own tool schemas)", [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": "Hello"}], tool_specs("student"))
    return out


if __name__ == "__main__":
    label, key, model, base = config.provider_settings()
    print(f"provider={config.LLM_PROVIDER}  model={model or '(not set)'}  base_url={base or '-'}  key={'set' if key else 'NOT SET'}  temperature={config.LLM_TEMPERATURE}\n")
    results = run_checks()
    for name, ok, info in results:
        print(f"  {'OK' if ok else 'FAIL':<4} {name}\n       {info}")
    bad = [r for r in results if not r[1]]
    print("\nAll steps passed." if not bad else "\nThe first FAIL above is the thing to fix. This output contains no API key, so it is safe to share.")
    sys.exit(1 if bad else 0)
