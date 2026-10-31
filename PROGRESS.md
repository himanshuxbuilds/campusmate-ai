# Remediation progress (resume from the first unchecked item)
Source of truth for what is done. Updated after every completed step.

- [x] 1 config, db engine, SQLAlchemy models
- [x] 2 Alembic migrations
- [x] 3 security package (passwords, sessions, rate limit, redaction, headers) + audit/trace services
- [x] 4 seed / init / ingest scripts, data into SQLite
- [x] 5 services: authorization, retrieval, memory, approvals
- [x] 6 tools registry + tool modules (Pydantic validation)
- [x] 7 Groq client + mock test double + context builder
- [x] 8 agent service
- [x] 9 routes + app factory
- [x] 10 frontend (UI code complete; NOT rendered in a browser here) (cookie auth, sidebar, staff dashboard)
- [x] 11 evaluation (service written; script next) service + run_evaluation script
- [x] 12 pytest suite (312 passed, 6 skipped = live Groq; 20/20 mutation checks killed)
- [x] 13 fix failures; legacy modules deleted; evaluation 24/24 on mock
- [x] 14 README, .env.example, pinned requirements, docs (architecture, security, workflow, retention, privacy, demo guide, data model, REMEDIATION_STATUS), samples

## Final state
- 326+ tests pass, 6 skipped (live Groq, needs a key). 20/20 mutation checks killed. Evaluation 24/24 on the OFFLINE MOCK only.
- NOT verified: the app against live Groq, real-browser UI, Windows execution (Linux verified), PostgreSQL.

## Provider migration
- xAI Grok removed; Groq (OpenAI-compatible, native tool calling) is the only real provider; `mock` kept for offline tests. Live Groq checks are still to be run by the user (scripts/check_llm.py).
