# Architecture and how it works

## Request lifecycle (one chat message)
| # | Step | Where |
|---|---|---|
| 1 | Authenticate: the HttpOnly cookie is looked up in `auth_sessions` (idle + absolute expiry); the identity (`Principal`) comes from the session, never from the request | `security/deps.py: authenticated` |
| 2 | Validate: CSRF token + Origin, JSON schema (`extra="forbid"`, length 1-2000), per-user rate limits | `deps.py`, `schemas.py: ChatBody`, `security/ratelimit.py` |
| 3 | Role: only `student` may chat; staff/admin get 403 (audited) | `deps.py: student_only` |
| 4 | Redact: secrets in the message are masked **before** anything else sees it; a `SECRET_SHARED` audit row records only the label | `services/agent_service.py`, `security/redaction.py` |
| 5 | Own the conversation: an existing conversation is fetched with `user_id = caller` in the query (someone else's ID is "not found") | `agent_service.get_owned_conversation` |
| 6 | Select context: relevant memory, budgeted history, system rules | `services/context_builder.py`, `memory_service.select_relevant` |
| 7 | Ask the model (Groq) what to do next, with the tool specs for this role | `services/llm_client.py` |
| 8 | If tool calls: validate name / role / arguments / ownership, run inside a savepoint, return a structured result | `tools/registry.py: run_tool` |
| 9 | Return the tool results to the model as **data**; loop up to `AGENT_MAX_ITERATIONS` | `agent_service.run_chat` |
| 10 | Final answer; rule-based memory safety net on the *redacted* text | `agent_service`, `memory_service.extract_with_rules` |
| 11 | Persist redacted messages, write the trace (latency, tokens, evidence), commit | `trace_service.py` |
| 12 | Return answer, sources, approval cards, tickets, trace summary (JSON, or server-sent events for live status) | `routes/chat_routes.py` |

## How the model decides which tool to use
Each model call carries (a) a system prompt with explicit rules (private data -> tools; rules and deadlines -> `search_student_policy`; general knowledge and greetings -> answer directly; sensitive changes -> confirm,
then `request_sensitive_change`), and (b) JSON-Schema function definitions generated from the Pydantic argument models. The model answers with either text or `tool_calls`. **The backend never trusts that choice**: the
tool name must exist, the caller's role must be allowed, the arguments must validate (extra fields are rejected), and the tool itself enforces ownership. The model sees only the tools its role may use
(a student sees 12; staff/admin see none because they cannot chat). Which tool a real model *picks* is unmeasured until you run the live evaluation; the mock provider is a rule-based test double.

## How the backend executes tools (`tools/registry.py`)
1. Unknown tool name -> `UNKNOWN_TOOL` (audited; the name is neutralised before it reaches a trace or the UI).
2. Role not allowed -> `FORBIDDEN` (audited as `ACCESS_DENIED`).
3. Arguments not a JSON object, or failing the Pydantic model (wrong type, unknown field, bad enum, too long) -> `INVALID_ARGUMENTS` with field-level messages only.
4. Run the handler inside a **savepoint**. A refusal (`Forbidden`), a business error (`ToolError`) or any unexpected exception rolls the savepoint back, so no partial change survives.
5. Return one envelope: `{"success": true, "data": ...}` or `{"success": false, "error": {"code", "message"}}`. Unexpected exceptions are logged with a correlation ID and reported as a generic `TOOL_FAILED`
   (no exception text, connection strings or stack traces reach the user, the model, the trace or the audit log).
6. Record the tool call in the trace (latency, status, error code; arguments are redacted) and commit so audit rows survive even when the tool failed.

## Persistent memory (`services/memory_service.py`)
* Table `memories`: `user_id`, `category` (preference, profile, support_context, interest, plan), `memory_key`, `memory_value`, `source`, timestamps, `expires_at`.
* **Written** by the model through `save_memory` (strict key enum) and by a rule-based safety net run on the redacted message. Every value is normalised to one line and rejected if it is longer than 200
  characters, contains `<` or `>`, a secret pattern, a credential word, or a long digit run, or is a temporary statement ("right now").
* **Selected** per question, never wholesale: answer-shaping preferences for substantive questions, the preferred name for greetings, contact preference for contact questions, any item whose words overlap
  the question; at most `MEMORY_MAX_SELECTED`.
* **Rendered** inside `<memory>...</memory>` as single-line data with angle brackets stripped (a stored value cannot close the block), and the prompt tells the model it is data, not instructions.
* Student-scoped everywhere: the API and tools use the session identity; another student's item ID is "not found". Creation and deletion are audited **without the value**. Items are listed and deletable in the UI.

## Policy retrieval (`services/ingest_service.py`, `retrieval_service.py`)
1. **Ingest**: the structured handbook (headings like `### F.3.2 | Title` with type, status, source, questions, keywords) becomes one chunk per entry; any other Markdown/text is split by heading and then into windows of
   `CHUNK_SIZE` words with `CHUNK_OVERLAP` overlap, cut at sentence ends, keeping `<!-- page: N -->` page markers. Metadata (section, page, status OFFICIAL/AUGMENTED, source) is stored in `policy_chunks`.
   Re-ingesting the same file is a no-op; a different file replaces the document.
2. **Search**: BM25 over text, boosted title/keywords/questions, a question-match bonus, a coverage filter, a preference for the student's programme, at most 2 chunks per section, top-k = 5.
3. **Abstain**: below the score threshold, or when most of the question's weight is on words that appear nowhere in the handbook, the result is empty and the model is told to say the handbook does not cover it.
4. **Evidence**: only the top-k chunks reach the model (never the whole handbook); their IDs, titles, status and scores are written to the trace and shown as "Sources" with clickable entry numbers.

## Context selection (`services/context_builder.py`)
Included: the system prompt (rules, identity as name and programme), relevant memory, the newest history turns that fit `CONTEXT_TOKEN_BUDGET` (older turns are dropped and replaced by a one-line summary of
what the student had asked), and the question. Never preloaded: fee/scholarship/profile records, policy text (fetched through tools, which authorize them). A trivial message therefore carries no private data and no handbook text.

## How student identity is enforced
Cookie -> `auth_sessions.token_hash` (HMAC of the token; the raw token is never stored) -> user -> `Principal`. No endpoint or tool takes a student ID as proof of identity. Tools accept an optional `student_id` only so a
*request for someone else* can be refused and audited; `resolve_student_target` allows only the caller's own ID or name. REST student routes have no identity parameter at all.

## How unauthorized requests are blocked
`authorization_service` decides before any data is read: `Forbidden` is raised, the savepoint rolls back, an `ACCESS_DENIED` audit row (actor, role, action, target, decision, trace ID, no private content) is
written, and the caller gets the same message whether or not the target exists. The model receives only `FORBIDDEN`. Role-restricted routes use the same service.

## How sensitive changes require approval
`request_sensitive_change` can only create an `approval_requests` row (validated, hashed, expiring). Staff approve or reject through `/api/approvals/{n}/approve|reject`; the change is executed by application code
in the same transaction as the status change. See `approval_workflow.md`.

## How audit records are generated
`audit_service.record` is called by the code that performs each action (login, denial, record access, ticket creation, sensitive request, approval decision, record modification, tool/LLM failure, memory
create/delete, conversation delete, admin action, secret shared, rate limit, CSRF rejection). Details pass through `redact_args` (PII masked, secrets removed); values are never logged, only field names. There is no
update or delete function. Agent **traces** (`agent_traces`) are separate: one per request with model and tool latency, iterations, tokens labelled `reported by provider` or `estimated (local)`, retrieval and memory references.

## How evaluation metrics are calculated (`services/evaluation_service.py`)
* Task success = scenarios passed / total x 100. A *blocked* unauthorized request counts as a pass for that scenario.
* Tool-selection correctness = scenarios whose expected tools were all used (and, where "no tool" is expected, none used) / scenarios with an expectation.
* Tool success rate = tool executions with `ok` / all attempted (refusals in the privacy scenarios count as unsuccessful executions, so this is below 100% by design).
* Privacy blocking rate = unauthorized scenarios passed / unauthorized scenarios (7). Approval enforcement rate = approval scenarios passed / approval scenarios (5).
* Latency = wall time of the scenario's chat calls. Tokens are summed from traces and split into provider-reported vs estimated. Observations are generated from the run's data.
* The A1-vs-A2 comparison re-runs the same scenarios with the agent-level controls patched off **inside the test harness**; it measures what the controls add, not model quality.
