"""Run the 24-scenario evaluation in an isolated temporary database and write JSON/CSV/Markdown reports to eval/results/.

    python scripts/run_evaluation.py                    # uses LLM_PROVIDER from .env (default: groq) -> refuses if Groq is not configured
    python scripts/run_evaluation.py --provider mock    # offline plumbing check (rule-based test double; tokens are estimates)
    python scripts/run_evaluation.py --provider groq    # real model via Groq (GROQ_API_KEY, GROQ_MODEL)
    python scripts/run_evaluation.py --provider groq    # the real model via Groq (GROQ_API_KEY, GROQ_MODEL)

Results are computed from what actually happened. If the provider is unavailable the script stops; it never substitutes another.
"""
import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config, db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["groq", "mock"], default=None)
    ap.add_argument("--no-baseline", action="store_true", help="skip the Assignment-1 configuration comparison run")
    ap.add_argument("--only", default="", help="comma-separated scenario numbers, e.g. 1,2,12")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.provider:
        config.LLM_PROVIDER = a.provider
    ok, reason = config.llm_configured()
    if not ok:
        print(f"Cannot run: {reason}. Configure it in .env, or run with --provider mock for the offline plumbing check.", file=sys.stderr)
        return 2
    config.DEMO_TODAY = config.DEMO_TODAY or "2026-10-30"
    config.ENV, config.IS_DEV, config.SESSION_COOKIE_SECURE = "test", True, False
    from app.services import llm_client
    llm_client.reset_client()
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{Path(tmp) / 'eval.db'}"
        config.DATABASE_URL = url
        from scripts.init_db import upgrade
        upgrade(url)
        db.init_engine(url)
        from app.services.ingest_service import ingest_policy
        from app.services.seed_service import seed_demo_data
        with db.session_scope() as s:
            seed_demo_data(s); ingest_policy(s)
        from app.services import evaluation_service as ev
        provider, model = llm_client.provider_label()
        report = ev.run_full(provider, model, compare_baseline=not a.no_baseline, only=[int(x) for x in a.only.split(",") if x.strip()] or None)
        paths = ev.write_reports(report, a.out)
        db.engine.dispose()
    m = report["metrics"]
    print(f"\nprovider={provider} model={model} tokens={report['token_source']}")
    for s_ in report["scenarios"]:
        print(f"  #{s_['id']:<2} {'PASS' if s_['passed'] else 'FAIL'}  {s_['latency_ms']:>5} ms  {s_['name']}" + ("" if s_["passed"] else f"\n        -> {s_['observed']}"))
    print(f"\ntask success {m['correctly_completed']}/{m['total_test_cases']} = {m['task_success_rate_pct']}%  |  tool selection {m['tool_selection_correct']}/{m['tool_selection_total']}  |  "
          f"privacy blocking {m['unauthorized_attempts_blocked']}/{m['unauthorized_attempts_tested']}  |  approval enforcement {m['sensitive_actions_handled_correctly']}/{m['sensitive_action_tests']}  |  "
          f"tool success {m['tool_executions_successful']}/{m['tool_executions_attempted']}")
    if "comparison" in report:
        c = report["comparison"]
        print(f"A1-config vs A2-config: functional {c['functional_success_a1_config']}% vs {c['functional_success_a2_config']}% | privacy {c['privacy_blocking_a1_config']}% vs {c['privacy_blocking_a2_config']}% | approvals {c['approval_enforcement_a1_config']}% vs {c['approval_enforcement_a2_config']}%")
    print("reports:", ", ".join(Path(p).name for p in paths.values()))
    return 0 if m["correctly_completed"] == m["total_test_cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
