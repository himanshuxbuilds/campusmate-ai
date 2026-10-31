# CampusMate AI

*Your campus, connected.* A tool-using AI agent for PGDM students of the Adani Institute of Digital Technology Management (Batch 2025-27).
One integrated application for **Agentic AI Engineering Assignment 1** (agent, tools, memory, retrieval, tracing, evaluation) and **Assignment 2** (identity,
privacy, authorization, human approval, audit).

* **LLM:** **Groq Cloud** through its OpenAI-compatible API with native function (tool) calling (`app/services/llm_client.py`). No silent fallback to another provider; `mock` exists only as an explicit offline test double.
* **Backend:** FastAPI, SQLAlchemy 2 + SQLite (any SQLAlchemy URL works), Alembic migrations, Pydantic validation.
* **Frontend:** plain HTML, CSS and JavaScript (no framework, no build step, no inline scripts; the UI never builds HTML from strings).
* **Data:** a structured, numbered student handbook (239 entries) stored in the database and searched with question-aware BM25.

> **Read this first: what has and has not been verified**
> * **Offline (automated, no network, no cost):** the test suite covers authentication, sessions, CSRF, authorization, approvals, audit, memory, retrieval, the database and migrations, the
>   frontend's static security properties, and the **Groq client plus the whole tool-calling loop against a local protocol stub**: request shape, native tool calls, multi-step and parallel tool
>   calls through the real tool registry, authorization and approval checks, retries, timeouts and error handling. This proves the application follows the OpenAI-compatible contract correctly.
> * **Live Groq, reported by you:** you independently verified from your Windows machine that your Groq key authenticates (HTTP 200), that a chat completion with `openai/gpt-oss-20b` works, and that
>   native function calling returns a correct tool call. That used your own script, not this application, and I have not seen its output.
> * **NOT yet verified: this application talking to Groq.** Nothing yet proves that the app's own requests and its 12 tool schemas are accepted by that model, or how well the model chooses tools.
>   Run the live checks in [Using Groq](#using-groq) to find out. Offline results never substitute for them.
> * Every recorded evaluation so far used the **offline mock provider**, a rule-based test double. It proves the application's plumbing and controls and says **nothing about the model's tool-selection
>   quality**. Its token counts are estimates and are labelled as such.
> * The sign-in page and chat UI have rendered correctly in your browser; other interactions have had only static checks and a Node render test.
> * `GROQ_MODEL` has **no default**: Groq retires models, so choose a current tool-calling model from <https://console.groq.com/docs/models>.

## Contents
1. [Quick start (Windows)](#quick-start-windows)  2. [Using Groq](#using-groq)  3. [Demo accounts](#demo-accounts-development-only)  4. [Features](#features)
5. [Architecture](#architecture)  6. [Security model](#security-model)  7. [Tests and evaluation](#tests-and-evaluation)  8. [Production deployment](#production-deployment)
9. [Project layout](#project-layout)  10. [Troubleshooting](#troubleshooting)  11. [Known limitations](#known-limitations)

## Quick start (Windows)
Requires Python 3.11 or newer (developed and tested on 3.12). Open **Command Prompt** in the project folder (in VS Code: *Terminal > New Terminal*, choose *Command Prompt*).

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env
```
Edit `.env` (see [Using Groq](#using-groq)). To try the application **without** a Groq key, set `LLM_PROVIDER=mock` in `.env`: an offline test double answers instead (the UI shows an
"offline test model" badge).

```bat
python scripts\init_db.py            :: create / upgrade the database with Alembic migrations
python scripts\seed_demo_data.py     :: fictional demo users and records (development only)
python scripts\ingest_policy.py      :: load the handbook into the database
python run.py                        :: start the server  ->  http://localhost:8000
```
`python run.py` also performs the three setup steps automatically on first start in development. Stop with `Ctrl+C`.

macOS/Linux: `source .venv/bin/activate`, `cp .env.example .env`, and use `/` in paths.

| Task | Command |
|---|---|
| Run the automated tests (no network, no cost) | `python -m pip install -r requirements-dev.txt` then `python -m pytest` |
| Run the 24-scenario evaluation (offline plumbing check) | `python scripts\run_evaluation.py --provider mock` |
| Run the same evaluation on the real model (**live**) | `python scripts\run_evaluation.py --provider groq` |
| Retrieval benchmark | `python scripts\eval_retrieval.py` |
| Regenerate sample traces/audit records from a real run | `python scripts\export_samples.py` |
| Delete data older than the retention policy | `python scripts\purge_retention.py` (or the admin API) |
| Create a real user (production; prompts for the password) | `python scripts\create_user.py --code S2026X001 --name "Name" --role student --programme PGDM-AIDS` |
| Inspect traces and audit events | sign in as `admin.campus` (or staff) and open the dashboard tabs, or `GET /api/admin/traces`, `GET /api/admin/audit-events` |

## Using Groq
1. Create an API key at <https://console.groq.com/keys> and choose a tool-calling model from <https://console.groq.com/docs/models> (this project's Groq connection was independently verified with `openai/gpt-oss-20b`).
2. In `.env`:
```
LLM_PROVIDER=groq
GROQ_API_KEY=your-key
GROQ_MODEL=openai/gpt-oss-20b
GROQ_BASE_URL=https://api.groq.com/openai/v1
```
3. Restart the server. `GET /api/health` shows `"llm": {"provider": "groq", "configured": true}` (it never reveals the key or model).

If the key or model is missing the application **still starts**: sign-in, records and approvals work and chat answers "AI not configured" (HTTP 503). It never switches to the mock on its own.

### Verify with the real model (LIVE: these make real requests and use a little of your Groq quota)
```bat
python scripts\check_llm.py                      :: 1. connection, model, a plain answer, one tool, then the app's full tool set
python run.py                                    :: 2. start the app, sign in as S2025AI001 / Demo@1101 and chat (see docs/demo_guide.md)
python scripts\run_evaluation.py --provider groq :: 3. the 24 scenarios against the real model (reports in eval\results\)
python -m pytest tests\test_live_groq.py -m integration -s   :: 4. six live tests
```
Only these measure the real model. A failure here is information (for example the model called a tool for "what is 2 + 2"), not necessarily a broken install; the report names the scenario and the reason.
On Groq's free tier a burst of requests can hit the tokens-per-minute limit (HTTP 429). The client retries (`LLM_MAX_RETRIES`) and then shows a retryable message; if it happens during the evaluation, wait a minute
or run a subset with `--only 1,2,3`.

## Demo accounts (development only)
Fictional people. Shown on the login page only when `SHOW_DEMO_ACCOUNTS=true` (the default in development; refused in production). Passwords exist only in `app/services/seed_service.py`
and are stored hashed.

| Username | Password | Role |
|---|---|---|
| `S2025AI001` Aarav Mehta | `Demo@1101` | student (AI & DS) |
| `S2025AI002` Ishita Rao | `Demo@1102` | student (AI & DS) |
| `S2025AI003` Rohan Desai | `Demo@1103` | student (AI & DS) |
| `S2025BD001` Priya Nair / `S2025BD002` Kabir Shah / `S2025BD003` Neha Iyer | `Demo@2101` / `Demo@2102` / `Demo@2103` | students (BDA) |
| `program.office` | `Staff@9000` | staff: contact, programme, elective, scholarship approvals |
| `accounts` | `Staff@9001` | staff: fee-correction, refund-bank, scholarship approvals |
| `admin.campus` | `Admin@9999` | administrator |

## Features
| Capability | Where | How it behaves |
|---|---|---|
| Agent loop with tool calling | `services/agent_service.py` | Bounded loop (`AGENT_MAX_ITERATIONS`), per-call timeout and bounded retries, malformed/unknown tool calls rejected and recovered from |
| 12 tools | `tools/` | `get_student_profile`, `check_fee_status`, `check_scholarship_status`, `search_student_policy`, `create_support_ticket`, `get_ticket_status`, `request_sensitive_change`, `get_current_academic_calendar`, plus `get_curriculum`, `compute_late_fee`, `save_memory`, `forget_memory`. Strict Pydantic arguments, role gate, one result envelope |
| Identity and sessions | `security/`, `routes/auth_routes.py` | Hashed passwords, HttpOnly cookie, server-side sessions (idle + absolute expiry), CSRF token + Origin check, login lockout, rate limits, security headers |
| Authorization | `services/authorization_service.py` | Decided in code before any data is touched; a student can only act on their own record; staff are scoped by category; uniform "not found / not authorized" answers reveal nothing |
| Human approval | `services/approval_service.py` | Six sensitive actions. States PENDING, APPROVED, EXECUTED, REJECTED, FAILED, EXPIRED; integrity hash; atomic decision; requester can never decide; scoped approvers |
| Memory | `services/memory_service.py` | Database-backed, per student, explicit categories, relevance-selected, expiring, reviewable and deletable, never stores secrets |
| Retrieval (RAG) | `services/retrieval_service.py`, `ingest_service.py` | Chunks and metadata in the database; threshold and abstention when the handbook does not cover a question; replaceable document and re-ingest script |
| Context selection | `services/context_builder.py` | Token budget, only relevant memory, trimmed history with a summary of what was dropped, data delimited and declared untrusted |
| Audit and tracing | `services/audit_service.py`, `trace_service.py` | Append-only audit rows; a trace per request with model/tool latency, tokens labelled provider-reported vs estimated, evidence IDs |
| Conversations | `routes/chat_routes.py` | Persisted, owned, listable, deletable; live status through server-sent events |
| Staff/admin dashboard | `static/app.js`, `routes/admin_routes.py` | Approval queue, tickets, access-denied events, tool failures, audit with filters, traces, evaluation results, user management (admin) |
| Evaluation | `services/evaluation_service.py` | The 24 specified scenarios through the real HTTP stack, spec formulas, A1-configuration vs A2-configuration comparison, JSON/CSV/Markdown reports |

## Architecture
```
 browser (HTML/CSS/JS) --cookie+CSRF--> FastAPI routes (auth, chat, student, memory, approvals, admin, policy)
                                           |  security deps: session, CSRF, roles, rate limits       security headers (middleware)
                                           v
                                    agent_service  --- context_builder --- memory_service (relevance selection)
                                           |                 |
                         Groq client  <----+----> tool registry --> authorization_service --> tools (fee, scholarship, policy, tickets, approvals, memory ...)
                         (or mock)                    |                                          |
                                                      v                                          v
                                 audit_service / trace_service  <---------------------  SQLAlchemy models (SQLite / PostgreSQL)
```
Read `docs/architecture.md` for how the model chooses tools, how the backend executes them, how memory, retrieval and context selection work, and how metrics are computed.

## Security model
Summarised in `docs/security.md`. The principles: the model proposes and the server decides; every tool call is validated, role-checked and ownership-checked in code; secrets are masked before
anything is stored, logged, traced or sent to the model; sensitive changes can only ever be *requested* by the agent; tool output, policy text, memory values and chat text are all treated as
untrusted data. There is **no setting, request field or code path that switches these controls off**; the evaluation's "Assignment 1 configuration" is a test-time patch inside the test harness.

## Tests and evaluation
* `python -m pytest`: 366 passed, 6 skipped. The 6 skipped tests are the optional LIVE Groq tests (they run only with your key). The suite was also validated by **mutation checks**: 20 deliberately introduced defects (cross-student
  access allowed, self-approval allowed, CSRF off, session expiry off, redaction off, audit of denials off, ...) were each caught by at least one test.
* `python scripts\run_evaluation.py --provider mock`: 24/24 scenarios pass (privacy blocking 7/7, approval enforcement 5/5, tool selection 12/12) **on the offline mock**. Reports are
  written to `eval/results/` (`latest.md`, `latest.json`, `latest_scenarios.csv`). With the Assignment-1 configuration (per-student resolution and the approval gate patched off in the test
  harness) privacy blocking falls to 42.9% and approval enforcement to 0%.
* Retrieval (`python scripts\eval_retrieval.py`), hit@3: **90%** on a 20-question test set, **87.5%** on a 32-question development set, 100% on the legacy set that is *not* independent (18 of its 50
  queries are copies of indexed questions). The test set scored 95% on its first and only untuned run, but the retriever then returned irrelevant entries for both out-of-scope questions; an
  abstention rule fixed that at a cost of one answerable question. Samples are small (20 to 35 questions); treat differences of one question as noise.

## Production deployment
`python run.py` and the built-in server are for development. For production:
* Set `APP_ENV=production`, a random `SECRET_KEY` (32+ chars), `SESSION_COOKIE_SECURE=true` and serve over **HTTPS** (a reverse proxy such as nginx or Caddy). The app refuses to start otherwise.
  Demo accounts, the mock provider and demo seeding are refused in production. Create users with `scripts/create_user.py`.
* Use an ASGI server: `uvicorn app.main:app --host 0.0.0.0 --port 8000` or `gunicorn -k uvicorn.workers.UvicornWorker app.main:app` (Linux). Windows: run uvicorn behind a service wrapper.
* **Use PostgreSQL** for anything beyond a single-process demo (`DATABASE_URL=postgresql+psycopg://...`, install the driver, run `python scripts\init_db.py`). SQLite allows one writer at a time
  (the code uses `BEGIN IMMEDIATE` so writers queue instead of failing) and every open SQLite transaction holds the write lock.
* The login lockout is database-backed, but the **request rate limiter is in-process**: with several workers each has its own counters. Use a shared store (e.g. Redis) or a proxy-level limiter.
* Schedule `python scripts\purge_retention.py` (see `docs/retention_policy.md`). Back up the database; audit rows are append-only by design (no edit/delete API exists).

## Project layout
```
app/            main.py (app factory), config.py, db.py, models.py (18 tables), schemas.py
  routes/       auth, chat (+SSE), student, memory, approvals, admin, policy
  security/     passwords, sessions, ratelimit, redaction, headers, deps, context
  services/     agent, llm_client (Groq), mock_llm (test double), context_builder, authorization, approval, memory, retrieval, ingest,
                audit, trace, evaluation, retention, fee, seed, catalog, clock
  tools/        registry + student, fee, scholarship, policy, ticket, approval, memory tools
migrations/     Alembic (initial schema)      scripts/   init_db, seed_demo_data, ingest_policy, run_evaluation, eval_retrieval, export_samples, purge_retention, create_user
static/         index.html, styles.css, app.js                 data/   handbook, calendar, curriculum, seed students
tests/          pytest suite (+ tests/js/render_test.js)       eval/   retrieval benchmarks, results/       docs/   architecture, security, workflow, demo guide, ...
```

## Troubleshooting
| Symptom | Fix |
|---|---|
| "The AI service rejected the server's credentials" | `GROQ_API_KEY` is not accepted. Copy the whole key (shown once at creation) from console.groq.com, no spaces or quotes. A key set in the Windows environment variables overrides `.env`: remove it and open a new terminal |
| "The AI service rejected the request" / "returned an unreadable answer" | Run `python scripts\check_llm.py`: it lists the models your key can use and checks yours is among them, sends the simplest raw request (with and without `temperature`) and prints the provider's exact reply, then tests a plain answer, one tool and our full tool set. The same reason is in the terminal running `python run.py` (look for `Groq request failed`) |
| Chat says "AI assistant is not configured" | Set `GROQ_API_KEY` and `GROQ_MODEL` in `.env` (or `LLM_PROVIDER=mock` for the offline test double) and restart |
| "The AI service rejected the configured model or the tool definitions" | `GROQ_MODEL` is wrong, retired or not tool-capable; run `python scripts\check_llm.py` and choose a current model from console.groq.com/docs/models |
| "The AI service is busy" (HTTP 503, rate limit) | Groq's per-minute token limit. Wait a minute and retry; the app already retries automatically |
| `database is locked` | Another process holds the SQLite file, or use PostgreSQL. Close other copies of the app / DB browsers |
| Sign-in returns "Too many failed attempts" | Wait `LOGIN_LOCKOUT_MINUTES` (default 15) |
| `Refusing to start` | You set `APP_ENV=production` without a strong `SECRET_KEY`, secure cookies, etc. The message lists what to fix |
| `no such table` | Run `python scripts\init_db.py` |
| Page loads but sign-in loops back to the login page over HTTP | `SESSION_COOKIE_SECURE=true` needs HTTPS; use `false` for local http |
| `ModuleNotFoundError` | Activate the virtual environment and run `python -m pip install -r requirements.txt` |

## Known limitations
* The application itself has not yet been exercised against live Groq (see the box at the top). Tool-selection quality, answer grounding and real latency/token figures are unmeasured until you run the live checks.
* The rule-based mock handles scripted phrasings only; it is a test double, not a stand-in for a model.
* Retrieval is lexical (no embeddings). It misses some paraphrases and abstains on some answerable questions with unusual words; the fallback is an honest "not covered" plus a ticket offer.
* Year 2 courses, Trimester V/VI dates and hostel/mess details in the handbook are marked AUGMENTED/TENTATIVE helpdesk guidance, not official AIDTM documents.
* SQLite single-writer; in-process rate limiter; no email/SMS notifications; no password-reset flow (an administrator creates users); not penetration-tested by a third party.
