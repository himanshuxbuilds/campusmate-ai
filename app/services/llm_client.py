"""LLM access through Groq Cloud's OpenAI-compatible chat-completions endpoint with native function (tool) calling.

The only real provider is Groq; `LLM_PROVIDER=mock` selects an offline test double. There is never a silent switch from one to the other.

HONESTY NOTE: the request/response handling, tool-call parsing, usage accounting, retries, timeouts and error classification here are
tested against a local protocol stub (tests/test_llm_client.py). That proves the client follows the OpenAI-compatible contract; it does NOT
prove how a particular Groq model behaves. Real-model behaviour is checked only by the live checks (scripts/check_llm.py,
scripts/run_evaluation.py --provider groq, tests/test_live_groq.py), which need your key. The model name is intentionally not defaulted.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app import config
from app.logging_config import logger


class LLMError(Exception):
    code, http_status, retryable = "LLM_ERROR", 502, False
    user_message = "The AI service had a problem. Please try again."

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.user_message)
        self.detail = detail


class LLMNotConfigured(LLMError):
    code, http_status, retryable = "LLM_NOT_CONFIGURED", 503, False
    user_message = "The AI assistant is not configured yet (the API key or model for the selected provider is missing). Everything else, such as signing in and approvals, still works."


class LLMAuthError(LLMError):
    code, http_status, retryable = "LLM_AUTH_FAILED", 502, False
    user_message = "The AI service rejected the server's credentials. Please contact the administrator."


class LLMRateLimited(LLMError):
    code, http_status, retryable = "LLM_RATE_LIMITED", 503, True
    user_message = "The AI service is busy right now. Please retry in a moment."


class LLMTimeout(LLMError):
    code, http_status, retryable = "LLM_TIMEOUT", 504, True
    user_message = "The AI service took too long to answer. Please retry."


class LLMUnavailable(LLMError):
    code, http_status, retryable = "LLM_UNAVAILABLE", 503, True
    user_message = "The AI service is temporarily unreachable. Please retry."


class LLMModelUnsupported(LLMError):
    code, http_status, retryable = "LLM_MODEL_UNSUPPORTED", 502, False
    user_message = "The AI service rejected the configured model or the tool definitions. The exact reason is in the server log."


class LLMRejected(LLMError):
    code, http_status, retryable = "LLM_REQUEST_REJECTED", 502, False
    user_message = ("The AI service rejected the request. The reason is in the server log (look for 'Groq request failed'), "
                    "or run: python scripts/check_llm.py")


class LLMMalformed(LLMError):
    code, http_status, retryable = "LLM_MALFORMED_RESPONSE", 502, True
    user_message = "The AI service returned an unreadable answer. Please retry."


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None            # None = the model sent arguments that were not valid JSON
    raw_arguments: str = ""


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = False                      # True = local estimate, False = reported by the provider
    model: str = ""
    latency_ms: int = 0
    assistant_message: dict[str, Any] = field(default_factory=dict)


def estimate_tokens(obj: Any) -> int:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return max(1, len(s) // 4)


_MODEL_PROBLEM = re.compile(r"(model\b[^.]{0,60}\b(not found|does not exist|doesn'?t exist|not available|unknown|invalid|deprecated|decommission|no access)"
                            r"|(unknown|invalid|no such) model|does not support (tool|function)|(tool|function)[ s]*(calling|use)[^.]{0,40}(not supported|unsupported))", re.I)


_KEY_PROBLEM = re.compile(r"((incorrect|invalid|wrong|missing|bad|unauthori[sz]ed)[^.]{0,25}api[ _-]?key|api[ _-]?key[^.]{0,40}(incorrect|invalid|not valid|not provided|missing))", re.I)


def _body_text(e: Exception) -> str:
    """The provider's explanation. The SDK's own message is often just 'Error code: 400', so read the parsed body and then the raw response.
    Handles OpenAI/Groq-style {"error": {"message": ...}} and flat {"code": ..., "error": "..."} bodies."""
    body = getattr(e, "body", None)
    out = ""
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            out = str(err.get("message") or "")
        elif isinstance(err, str):
            out = (f"{body['code']}: " if body.get("code") else "") + err
        elif body.get("message"):
            out = str(body["message"])
    elif isinstance(body, str):
        out = body
    resp = getattr(e, "response", None)
    if not out:
        try:
            out = resp.text if resp is not None else ""
        except Exception:                                                # noqa: BLE001  (streamed/unreadable response)
            out = ""
    try:                                                                 # some providers' flat bodies: the SDK keeps the "error" text but drops the top-level "code"
        raw = resp.json() if resp is not None else None
        if isinstance(raw, dict) and isinstance(raw.get("code"), str) and raw["code"] not in out:
            out = f"{raw['code']}: {out}"
    except Exception:                                                    # noqa: BLE001  (body is not JSON)
        pass
    return " ".join(out.split())


def _detail(e: Exception) -> str:
    """Provider error text for the server log / audit detail. The configured key and any secret-shaped text are redacted first."""
    from app.security.redaction import redact_secrets
    status = getattr(e, "status_code", None)
    msg = getattr(e, "message", None) or str(e)
    code = getattr(e, "code", None)                                      # e.g. Groq's "tool_use_failed": lives in the body, not in the message
    extra = _body_text(e)
    if extra and extra not in msg:
        msg = f"{msg} - {extra}"
    text = " ".join(f"{type(e).__name__}{' HTTP ' + str(status) if status else ''}{' [' + str(code) + ']' if code else ''}: {msg}".split())
    if config.GROQ_API_KEY:
        text = text.replace(config.GROQ_API_KEY, "[REDACTED KEY]")
    return redact_secrets(text)[0][:500]


def _clean(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = ("role", "content", "tool_calls", "tool_call_id")
    return [{k: v for k, v in m.items() if k in keep} for m in messages]


class OpenAICompatibleClient:
    """Chat-completions client with function calling for any OpenAI-compatible endpoint."""
    name = "openai-compatible"

    def __init__(self) -> None:
        ok, reason = config.llm_configured()
        if config.LLM_PROVIDER != self.name or not ok:
            raise LLMNotConfigured(reason)
        self.label, key, self.model, base_url = config.provider_settings()
        self._no_temperature = False                                       # set if the provider rejects the parameter
        from openai import OpenAI
        self._client = OpenAI(api_key=key, base_url=base_url, timeout=config.AGENT_TIMEOUT_SECONDS, max_retries=max(0, config.LLM_MAX_RETRIES))

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> LLMResponse:
        import openai
        kwargs: dict[str, Any] = {"model": self.model, "messages": _clean(messages)}
        if config.LLM_TEMPERATURE is not None and not self._no_temperature:
            kwargs["temperature"] = config.LLM_TEMPERATURE
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        t0 = time.perf_counter()
        for attempt in (1, 2):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                break
            except openai.APIError as e:                                   # every provider/network failure ends here: the cause goes to the log, the user gets generic text
                detail = _detail(e)
                logger.warning("%s request failed: %s", self.label, detail)
                if attempt == 1 and isinstance(e, openai.BadRequestError) and "temperature" in kwargs and "temperature" in detail.lower():
                    kwargs.pop("temperature"); self._no_temperature = True                      # some models reject it: retry once without, and remember
                    logger.warning("%s rejected the temperature parameter: retrying without it (set LLM_TEMPERATURE=none to stop sending it)", self.label)
                    continue
                raw_body = str(getattr(e, "body", "") or "")
                tool_failed = isinstance(e, openai.BadRequestError) and ("tool_use_failed" in detail or "tool_use_failed" in raw_body or "failed_generation" in raw_body)
                if attempt == 1 and tool_failed:                             # Groq: the model wrote an invalid tool call. A second sample usually succeeds.
                    logger.warning("%s: the model produced a malformed tool call; retrying once", self.label)
                    continue
                if isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)) or (isinstance(e, openai.BadRequestError) and _KEY_PROBLEM.search(detail)):
                    raise LLMAuthError(detail) from None                     # some providers report a bad key as HTTP 400, not 401
                if isinstance(e, openai.RateLimitError): raise LLMRateLimited(detail) from None
                if isinstance(e, openai.APITimeoutError): raise LLMTimeout(detail) from None
                if isinstance(e, openai.APIConnectionError): raise LLMUnavailable(detail) from None
                if isinstance(e, openai.NotFoundError) or (isinstance(e, openai.BadRequestError) and _MODEL_PROBLEM.search(detail)):
                    raise LLMModelUnsupported(detail) from None
                if tool_failed:
                    raise LLMMalformed(detail) from None                     # still malformed after the automatic retry: the user may retry
                if getattr(e, "status_code", 500) < 500:
                    raise LLMRejected(detail) from None                      # e.g. an unsupported parameter or tool-schema keyword
                raise LLMUnavailable(detail) from None
        latency = round((time.perf_counter() - t0) * 1000)
        if not getattr(resp, "choices", None):
            logger.warning(self.label + " response had no choices: %s", _detail(Exception(str(resp)[:300])))
            raise LLMMalformed("no choices in response")
        msg = resp.choices[0].message
        calls: list[ToolCall] = []
        for tc in (msg.tool_calls or []):
            raw = tc.function.arguments or "{}"
            try:
                parsed = json.loads(raw)
                parsed = parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                parsed = None
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=parsed, raw_arguments=raw))
        assistant: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if calls:
            assistant["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw_arguments}} for c in calls]
        usage = getattr(resp, "usage", None)
        reported = usage is not None and getattr(usage, "prompt_tokens", None) is not None
        return LLMResponse(text=msg.content or "", tool_calls=calls, estimated=not reported, model=getattr(resp, "model", None) or self.model, latency_ms=latency,
                           input_tokens=usage.prompt_tokens if reported else estimate_tokens(kwargs["messages"]),
                           output_tokens=(getattr(usage, "completion_tokens", 0) or 0) if reported else estimate_tokens(assistant), assistant_message=assistant)


class GroqClient(OpenAICompatibleClient):
    name = "groq"


_client: Any = None
_sig: tuple | None = None


def reset_client() -> None:
    global _client, _sig
    _client, _sig = None, None


def provider_label() -> tuple[str, str]:
    """(provider, model) for traces without constructing a client."""
    return (config.LLM_PROVIDER, "mock-planner-v3" if config.LLM_PROVIDER == "mock" else config.provider_settings()[2])


def get_client() -> Any:
    """Raises LLMNotConfigured when the selected provider is not configured. Never falls back to another provider."""
    global _client, _sig
    sig = (config.LLM_PROVIDER, *config.provider_settings(), config.AGENT_TIMEOUT_SECONDS, config.LLM_MAX_RETRIES)
    if _client is None or _sig != sig:
        if config.LLM_PROVIDER == "mock":
            from app.services.mock_llm import MockClient
            _client = MockClient()
        elif config.LLM_PROVIDER == "groq":
            _client = GroqClient()
        else:
            raise LLMNotConfigured(f"unsupported LLM_PROVIDER '{config.LLM_PROVIDER}'")
        _sig = sig
    return _client
