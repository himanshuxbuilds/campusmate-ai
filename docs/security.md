# Security model

**Principle: the model proposes, the server decides.** Nothing that matters is left to the prompt.

| Threat | Control | Verified by |
|---|---|---|
| Student reads/changes another student's data | `authorization_service.resolve_student_target` before any data access; REST routes have no identity parameter | `test_authorization.py`, scenarios 12, 13, evaluation; mutation M01 |
| A request field switches protections off | `extra="forbid"` request models; **no switch exists in the app** (the A1 baseline is a test-harness patch) | `test_smuggled_control_fields_are_rejected_not_ignored`, `test_the_application_has_no_baseline_switch_of_its_own` |
| Prompt injection (in chat, policy text, memory, tool output) | Server-side enforcement independent of the model; untrusted-data rules and delimiters; memory values single-line with `<>` stripped | `test_a_model_that_asks_for_another_students_data_is_refused_by_the_server`, `test_memory_cannot_break_out_of_its_delimiter_even_if_a_bad_row_exists` |
| Credential theft / brute force | Hashed passwords (scrypt), per-account and per-IP lockout, generic errors, constant-ish timing | `test_auth.py`; M13 |
| Session theft / fixation | Opaque token in an HttpOnly, SameSite=Lax cookie; only an HMAC stored; fresh token at every login; idle + absolute expiry; revoke on logout/deactivation | `test_logout_revokes_the_session_server_side`, `test_new_login_issues_a_fresh_token...`, M09 |
| CSRF / cross-site requests | `X-CSRF-Token` per session on every state-changing request + Origin check | `test_state_changing_requests_need_the_csrf_token`, `test_cross_origin_state_change_is_rejected`; M08 |
| XSS | The UI builds DOM nodes with `textContent` only (no `innerHTML`); strict CSP (`script-src 'self'`, no inline); hostile tool names neutralised server-side | `test_frontend_static.py` (executes the renderer against payloads in Node), `test_unknown_and_hostile_tool_names_are_neutralised` |
| Secret leakage (store/log/trace/audit/model) | `redact_secrets` before anything; redacting log formatter (covers tracebacks and URL credentials); memory value validation | scenarios 7, 10, 20; `test_no_credentials_or_personal_values_ever_reach_the_audit_log`; M05, M14, M18 |
| Unsafe sensitive changes | Request-only tool, human approval, integrity hash, expiry, scoped approvers, atomic one-time execution | `test_approvals.py`, scenarios 14-18; M02, M03, M04, M15, M16 |
| Tool misuse (bad args, hostile names, loops) | Strict Pydantic models; role gate; max iterations; savepoint per tool | `test_tools.py`, `test_agent.py`; M12, M17 |
| Error/internal disclosure | Generic errors with a reference ID; validation errors never echo input; no stack traces | `test_unexpected_failure_is_generic_audited_and_leaks_nothing`, `test_validation_errors_never_echo_the_submitted_input` |
| Cost / abuse of the paid model | Per-user chat and API rate limits (in-process; use a shared store behind several workers) | `test_chat_rate_limit` |
| Repudiation | Append-only audit with actor, decision, outcome, trace and correlation IDs | `test_audit.py`; M10 |
| Concurrency (double approval) | Compare-and-set + `BEGIN IMMEDIATE` | `test_simultaneous_approvals_execute_exactly_once`; M20 |
| Misconfiguration in production | Startup validation refuses weak settings; demo accounts and mock refused | `test_config.py` |

## Defects found while building this and fixed (kept here so the reasoning is visible)
* A stored memory value containing `</memory>` could close the prompt's data block: now rejected at save time and neutralised at render time.
* Server logs printed exception tracebacks containing a DSN password: the log formatter now redacts the final text; URL credentials are a redaction pattern.
* Two simultaneous approvals could crash with `database is locked` on SQLite (deferred read-then-write): transactions now begin `IMMEDIATE`.
* Non-object tool arguments were silently treated as "no arguments": now rejected.
* A failing audit write inside the tool error handler could turn a tool failure into a 500: the handler is now best-effort and logs the audit failure.
* A relative SQLite path depended on the current directory: now resolved against the project root.

## Not covered / residual risk
No third-party penetration test. The request rate limiter is per process. SQLite is single-writer (use PostgreSQL in production). There is no MFA and no password-reset flow. The application's Groq integration has not been
exercised against the live service. The browser UI has not been exercised in a real browser (static analysis and a Node render test only). TLS is the reverse proxy's job.
