"""Generate docs/data_model.md (from the live SQLAlchemy metadata) and docs/REMEDIATION_STATUS.md. The latter FAILS if any test it cites
does not exist, so every claim is checkable.   python scripts/generate_docs.py"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import models  # noqa: F401
from app.db import Base

# ------------------------------------------------------------------ data model
lines = ["# Data model", "", "Generated from the SQLAlchemy models by `scripts/generate_docs.py` (migrations: `migrations/`). Foreign keys point at `users.user_code`.", ""]
for name, t in sorted(Base.metadata.tables.items()):
    lines += [f"## `{name}`", "", "| Column | Type | Constraints |", "|---|---|---|"]
    for c in t.columns:
        cons = [x for x in ("PK" if c.primary_key else "", "NOT NULL" if not c.nullable and not c.primary_key else "", "UNIQUE" if c.unique else "", "INDEX" if c.index else "",
                            "FK " + ", ".join(sorted(f.target_fullname for f in c.foreign_keys)) if c.foreign_keys else "") if x]
        lines.append(f"| {c.name} | {str(c.type)} | {', '.join(cons)} |")
    extra = [str(x.name) for x in t.constraints if getattr(x, "name", None) and x.__class__.__name__ in ("CheckConstraint", "UniqueConstraint")]
    if extra:
        lines += ["", "Table constraints: " + ", ".join(f"`{e}`" for e in extra)]
    lines.append("")
(ROOT / "docs" / "data_model.md").write_text("\n".join(lines), encoding="utf-8")

# ------------------------------------------------------------------ remediation status
tests_src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "tests").glob("test_*.py"))
def check(names):
    missing = [n for n in names if not re.search(rf"def {re.escape(n)}\b", tests_src)]
    if missing:
        sys.exit(f"cited tests that do not exist: {missing}")

FIXED, PART, OPEN = "Fixed", "Partly", "Open"
ROWS = [
 ("SEC-01 Critical", "Safety switch (`controls_enabled`) was a request field", FIXED, "Removed. Strict request models reject unknown fields; the app contains no switch (the A1 baseline is a test-harness patch)", ["test_smuggled_control_fields_are_rejected_not_ignored", "test_the_application_has_no_baseline_switch_of_its_own"]),
 ("SEC-02 High", "History keyed by client-chosen `session_id`", FIXED, "Server-issued 32-hex conversation IDs owned by the user; foreign = missing = 404", ["test_other_students_cannot_read_post_into_or_delete_my_conversation", "test_foreign_and_missing_conversation_ids_are_indistinguishable", "test_malformed_conversation_ids_are_rejected"]),
 ("SEC-03 High", "No login throttling", FIXED, "Per-account and per-IP lockout, database-backed, audited", ["test_lockout_after_repeated_failures_then_even_correct_password_is_refused", "test_lockout_is_per_account_not_global"]),
 ("SEC-04 High", "Plaintext credentials", FIXED, "scrypt hashes; seed data holds no passwords", ["test_passwords_are_hashed_never_plaintext"]),
 ("SEC-05 High", "Sessions never expired", FIXED, "DB sessions, idle + absolute expiry, revocation", ["test_absolute_session_expiry", "test_idle_session_expiry", "test_logout_revokes_the_session_server_side"]),
 ("SEC-06 High", "No rate limiting", PART, "Per-user chat/API limits (in-process: use a shared store with several workers)", ["test_chat_rate_limit"]),
 ("SEC-07 High", "Internal error text shown to users / in audit", FIXED, "Generic errors + reference ID; exceptions logged server-side with redaction", ["test_unexpected_failure_is_generic_audited_and_leaks_nothing", "test_failure_handler_survives_a_failing_audit_write", "test_validation_errors_never_echo_the_submitted_input"]),
 ("SEC-08 High", "API keys/tokens persisted to memory", FIXED, "Broader redaction; memory values validated (no secrets, credential words, tags, long digits)", ["test_sensitive_or_invalid_values_are_never_stored", "test_no_credentials_or_personal_values_ever_reach_the_audit_log"]),
 ("SEC-09 Medium", "Public user directory", FIXED, "Endpoint removed; free-text login", ["test_no_public_user_directory"]),
 ("SEC-10 Medium", "No security headers", FIXED, "CSP, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, HSTS when secure", ["test_security_headers_present", "test_csp_blocks_what_the_ui_does_not_need"]),
 ("SEC-11 Medium", "Trace panel rendered the model's tool name unescaped", FIXED, "UI builds DOM only (no `innerHTML`); tool names neutralised server-side", ["test_app_js_never_builds_html_from_strings_or_evals_code", "test_markdown_renderer_turns_hostile_input_into_inert_text", "test_unknown_and_hostile_tool_names_are_neutralised"]),
 ("SEC-12 Medium", "Approval proposal integrity / non-atomic persistence", FIXED, "Proposal hash; savepoint execution; one transaction", ["test_a_tampered_proposal_is_never_executed", "test_partial_writes_are_rolled_back_when_execution_fails_midway"]),
 ("SEC-13 Medium", "Approvals never expired", FIXED, "`expires_at` + EXPIRED state", ["test_expired_requests_cannot_be_approved", "test_listing_marks_overdue_requests_expired"]),
 ("SEC-14 Medium", "Prompt-injection posture", FIXED, "Untrusted-data rules + delimiters; memory sanitised (a delimiter-escape hole found during testing was fixed)", ["test_system_prompt_marks_untrusted_data_and_delimits_memory", "test_memory_cannot_break_out_of_its_delimiter_even_if_a_bad_row_exists", "test_a_model_that_asks_for_another_students_data_is_refused_by_the_server"]),
 ("SEC-15 Medium", "Tool arguments not validated", FIXED, "Strict Pydantic models per tool; non-objects rejected", ["test_bad_arguments_are_rejected_with_a_clean_error", "test_non_object_arguments_are_rejected_even_for_all_optional_tools"]),
 ("SEC-16 Medium", "Unscoped staff / no admin tier", FIXED, "student/staff/admin roles; per-staff approval and ticket scopes; admin-only user management", ["test_approver_scope_is_enforced_for_listing_and_deciding", "test_staff_ticket_visibility_is_scoped_by_category", "test_only_admin_can_manage_users_and_purge"]),
 ("SEC-17 Low", "Victim could read the attacker's ID in audit", FIXED, "Students have no audit access at all", ["test_students_are_denied_staff_and_admin_endpoints_and_it_is_audited"]),
 ("SEC-18 Medium", "PII in traces/audit", FIXED, "Argument redaction; audit stores field names only", ["test_trace_arguments_are_redacted", "test_each_approved_change_is_audited_exactly_once_with_field_names_only"]),
 ("SEC-19 Low", "Duplicate tickets", FIXED, "Idempotency window + unique numbers", ["test_ticket_creation_is_database_backed_and_idempotent", "test_duplicate_ticket_within_window_returns_the_existing_one"]),
 ("SEC-20 Low", "Unbounded inputs", FIXED, "Length limits everywhere (messages, tickets, memory, tool args)", ["test_message_length_and_format_are_validated"]),
 ("SEC-21 Low", "Public docs / health detail", FIXED, "Docs off outside development; health hides provider details beyond configured yes/no", ["test_api_docs_can_be_disabled", "test_health_is_public_and_hides_secrets"]),
 ("SEC-22 Low", "App refused to start without an LLM key", FIXED, "Starts; chat returns 503 'not configured'; never falls back to the mock", ["test_missing_llm_key_does_not_stop_the_application_from_starting", "test_unconfigured_groq_disables_chat_cleanly_without_falling_back"]),
 ("SEC-23", "A Groq key appeared in an earlier screenshot", OPEN, "NOT a code issue: rotate that key at the provider. Nothing in the repository contains it", ["test_no_secret_shaped_strings_are_committed"]),
 ("TS-07 / DB-*", "No database; data in JSON/RAM; no conversations", FIXED, "SQLAlchemy 2 + Alembic, 18 tables, constraints and indexes, conversations persisted", ["test_migration_creates_every_model_table_and_downgrades_cleanly", "test_models_and_migration_have_not_drifted", "test_spec_tables_exist", "test_foreign_keys_are_enforced", "test_check_constraints_reject_invalid_rows", "test_unique_constraints", "test_expected_indexes_exist"]),
 ("TS-12 / TS-13", "No pytest, no logging", FIXED, "pytest suite; `logging` with a redacting formatter", ["test_event_coverage_over_a_full_session", "test_full_run_through_the_real_stack_passes_all_24_and_the_baseline_is_measurably_weaker"]),
 ("TS-01..03", "LLM provider (migrated from xAI Grok to Groq)", PART, "Groq client (GROQ_API_KEY/GROQ_MODEL/GROQ_BASE_URL) with native tool calling, retries, timeouts and error handling, tested against a local protocol stub. xAI settings removed. **NOT verified by this application against live Groq until `scripts/check_llm.py` and the live tests are run with your key**; model name intentionally unset", ["test_request_format_matches_the_function_calling_contract", "test_a_tool_call_executes_the_registered_backend_tool_and_usage_is_provider_reported", "test_multi_step_tool_chain_runs_each_step_through_the_registry", "test_missing_key_or_model_means_not_configured_never_mock", "test_no_xai_settings_remain"]),
 ("TL-02..07", "Tool names, ticket status, duplicate prevention, missing-record handling, spec scholarship statuses, course change / fee correction", FIXED, "Spec tool names; `get_ticket_status`; idempotent tickets; clear 'unavailable' results; spec status vocabulary; six sensitive actions", ["test_registry_has_the_specified_tools_and_only_students_see_them", "test_ticket_status_owner_only_and_uniform_not_found", "test_missing_fee_record_is_reported_not_invented", "test_approve_elective_change_drops_old_and_registers_new", "test_approve_fee_correction_keeps_fee_record_consistent"]),
 ("MEM-*", "Memory: relevance selection, categories, expiry, creation audit, spec example, restart survival", FIXED, "Relevance-selected, categorised, expiring, audited without values", ["test_selection_returns_only_relevant_memory", "test_expiry_purges_old_context_items", "test_audit_records_memory_events_without_the_value", "test_spec_memory_scenario_across_conversations", "test_memory_survives_an_application_restart"]),
 ("RAG-*", "Chunk size/overlap, page numbers, replaceable document, threshold, honest benchmark", FIXED, "Generic splitter, re-ingest script, abstention rule, independent benchmarks (see README for the measured numbers and their limits)", ["test_splitter_respects_size_overlap_headings_and_pages", "test_ingest_is_idempotent_replaceable_and_searchable", "test_out_of_scope_or_empty_questions_return_nothing", "test_held_out_benchmark_quality", "test_fresh_test_benchmark_quality"]),
 ("CX-*", "Token budget, summarisation, untrusted-data labelling", FIXED, "Budgeted history with a one-line summary of omitted turns; delimiters and rules", ["test_long_history_is_trimmed_to_the_budget_with_a_summary", "test_trivial_message_context_has_no_private_data_and_no_handbook"]),
 ("AP-*", "Approval states EXECUTED/EXPIRED, transactions, integrity, FAILED handling", FIXED, "Full state machine (see approval_workflow.md)", ["test_state_machine_terminal_states_have_no_exits", "test_failed_execution_is_recorded_as_failed_and_changes_nothing", "test_duplicate_decisions_cannot_re_execute"]),
 ("TR-*", "Audit coverage (login failure, logout, memory, LLM failure), retention policy", FIXED, "All event types emitted and tested; retention policy documented and implemented", ["test_event_coverage_over_a_full_session", "test_tool_and_llm_failures_are_audited", "test_purge_removes_only_data_older_than_the_policy"]),
 ("EV-*", "Hard-coded observations, circular retrieval benchmark, different A1/A2 cases, no tool-success metric", FIXED, "Observations derived from data; independent benchmarks; one scenario list with an A1 subset; tool success rate and tool-error count; baseline comparison", ["test_observations_are_derived_and_label_the_mock", "test_formulas_use_the_spec_definitions_and_report_denominators", "test_legacy_benchmark_still_works_but_is_flagged_non_independent"]),
 ("UI-*", "Sidebar, retry, show/hide password, profile + memory, staff dashboard, approval card, escaping", PART, "Implemented. **Not exercised in a real browser**: only static checks and a Node render test", ["test_ui_has_the_required_elements", "test_login_form_has_show_hide_password_labels_and_no_demo_credentials_in_static_files", "test_app_js_parses"]),
]
SCEN = {1: "test_general_questions_use_no_tools", 2: "test_the_right_tool_is_chosen_and_used", 3: "test_the_right_tool_is_chosen_and_used", 4: "test_the_right_tool_is_chosen_and_used", 5: "test_support_ticket_flow_reports_the_real_ticket_number",
        6: "test_spec_memory_scenario_across_conversations", 7: "test_messages_are_stored_redacted", 8: "test_policy_search_logs_evidence_and_admits_gaps", 9: "test_missing_fee_record_is_reported_not_invented",
        10: "test_unexpected_failure_is_generic_audited_and_leaks_nothing", 11: "test_fees_match_the_database_record", 12: "test_cross_student_reads_are_blocked_in_chat_and_nothing_leaks",
        13: "test_modifying_another_students_record_is_blocked_and_creates_no_request", 14: "test_sensitive_change_needs_confirmation_then_files_a_request_and_changes_nothing", 15: "test_rejection_leaves_the_record_untouched_and_is_final",
        16: "test_approve_contact_details", 17: "test_requester_can_never_decide_their_own_request", 18: "test_simultaneous_approvals_execute_exactly_once", 19: "test_students_cannot_see_or_delete_each_others_memory",
        20: "test_no_credentials_or_personal_values_ever_reach_the_audit_log", 21: "test_other_students_cannot_read_post_into_or_delete_my_conversation",
        22: "test_students_are_denied_staff_and_admin_endpoints_and_it_is_audited", 23: "test_a_model_that_asks_for_another_students_data_is_refused_by_the_server", 24: "test_each_approved_change_is_audited_exactly_once_with_field_names_only"}
check([t for r in ROWS for t in r[4]] + list(SCEN.values()))
out = ["# Remediation status", "", "Maps every finding of the Requirement Traceability Audit to what was done and the tests that prove it. `scripts/generate_docs.py` **fails if a cited test does not exist**.",
       "'Fixed' means implemented and covered by an automated test that passes against the **offline mock provider and the local Groq protocol stub**. It does not mean verified against live xAI or in a real browser; see the Open and Partly rows.", "",
       "| Finding | Problem | Status | Fix | Tests |", "|---|---|---|---|---|"]
for f, p, s, fix, tests in ROWS:
    out.append(f"| {f} | {p} | **{s}** | {fix} | {'<br>'.join('`' + t + '`' for t in tests)} |")
out += ["", "## The 24 specified evaluation scenarios", "", "Each is implemented in `app/services/evaluation_service.py` (run through the real HTTP stack by `scripts/run_evaluation.py`) and has a direct pytest counterpart.", "",
        "| # | Evaluation scenario | pytest counterpart |", "|---|---|---|"]
from app.services import evaluation_service as ev
for sc in sorted(ev.SCENARIOS, key=lambda s: s["id"]):
    out.append(f"| {sc['id']} | {sc['name']} | `{SCEN[sc['id']]}` |")
out += ["", "## Still open or only partly achieved", "",
        "1. **Live Groq**: the application has not been run against live Groq here. Run `python scripts/check_llm.py`, `python scripts/run_evaluation.py --provider groq` and `tests/test_live_groq.py` with your key; until then tool-selection quality, grounding and real latency/tokens are unmeasured.",
        "2. **Browser UI**: not rendered in a real browser. Do a manual pass of the demo guide.",
        "3. **Rotate the Groq key** that appeared in an earlier screenshot (SEC-23).",
        "4. **Scale**: SQLite is single-writer and the request rate limiter is in-process; use PostgreSQL and a shared limiter for multi-worker production.",
        "5. **Retrieval** is lexical; hit@3 is 87.5-90% on small independent sets and it abstains on some answerable questions with unusual words.",
        "6. **Windows**: instructions are written for Windows but were exercised on Linux (clean-virtualenv install, full suite, real server).",
        "7. No MFA, no password-reset flow, no third-party penetration test. Year-2/Trimester V-VI/hostel content in the handbook is AUGMENTED helpdesk guidance."]
(ROOT / "docs" / "REMEDIATION_STATUS.md").write_text("\n".join(out), encoding="utf-8")
print("wrote docs/data_model.md and docs/REMEDIATION_STATUS.md;", len(ROWS), "findings,", len(SCEN), "scenarios; every cited test exists")
