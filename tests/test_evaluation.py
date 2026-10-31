import csv
import json

import pytest

from app import config
from app.services import authorization_service as authz
from app.services import evaluation_service as ev
from app.services.evaluation_service import ScenarioResult


def R(i, passed=True, **kw):
    return ScenarioResult(id=i, name=f"s{i}", group=kw.pop("group", "A1"), passed=passed, **kw)


def test_formulas_use_the_spec_definitions_and_report_denominators():
    rs = [R(1, True, expected_tools=[], strict_no_tools=True, tool_choice_correct=True, tool_attempts=0),
          R(2, True, expected_tools=["a"], tool_choice_correct=True, tool_attempts=2, tool_successes=2),
          R(3, False, expected_tools=["a"], tool_choice_correct=False, tool_attempts=1, tool_successes=0, tool_errors=1),
          R(12, True, unauthorized=True, tool_attempts=1, tool_successes=0),                        # BLOCKING a bad request is a SUCCESS for the scenario
          R(13, False, unauthorized=True), R(14, True, sensitive=True), R(15, True, sensitive=True), R(16, False, sensitive=True)]
    m = ev.compute_metrics(rs)
    assert (m["total_test_cases"], m["correctly_completed"], m["task_success_rate_pct"]) == (8, 5, 62.5) and m["task_success_formula"] == "5 / 8 x 100"
    assert (m["tool_selection_correct"], m["tool_selection_total"], m["tool_selection_correctness_pct"]) == (2, 3, 66.7)
    assert (m["tool_executions_attempted"], m["tool_executions_successful"], m["tool_success_rate_pct"], m["tool_errors"]) == (4, 2, 50.0, 2)
    assert (m["unauthorized_attempts_tested"], m["unauthorized_attempts_blocked"], m["privacy_blocking_rate_pct"]) == (2, 1, 50.0)
    assert (m["sensitive_action_tests"], m["sensitive_actions_handled_correctly"], m["approval_enforcement_rate_pct"]) == (3, 2, 66.7)


def test_rates_are_none_not_zero_when_there_is_nothing_to_measure():
    m = ev.compute_metrics([R(1, True)])
    assert m["privacy_blocking_rate_pct"] is None and m["approval_enforcement_rate_pct"] is None and m["tool_success_rate_pct"] is None and m["tool_selection_correctness_pct"] is None


def test_latency_statistics_and_token_split():
    rs = [R(1, latency_ms=100, input_tokens=10, output_tokens=2, tokens_estimated=True), R(2, latency_ms=300, input_tokens=50, output_tokens=7, tokens_estimated=False),
          R(3, latency_ms=200, input_tokens=5, output_tokens=1, tokens_estimated=True)]
    m = ev.compute_metrics(rs)
    assert (m["avg_latency_ms"], m["median_latency_ms"], m["max_latency_ms"]) == (200.0, 200.0, 300)
    assert (m["input_tokens_provider_reported"], m["output_tokens_provider_reported"], m["input_tokens_estimated"], m["output_tokens_estimated"]) == (50, 7, 15, 3)


def test_observations_are_derived_and_label_the_mock():
    rs = [R(1, True, input_tokens=5, tokens_estimated=True), R(2, False)]
    obs = ev.observations("mock", ev.compute_metrics(rs), rs)
    assert any("offline mock" in o and "NOT model quality" in o for o in obs) and any("1 of 2 scenarios failed: #2" in o for o in obs) and any("local estimates" in o for o in obs)
    assert not any("offline mock" in o for o in ev.observations("groq", ev.compute_metrics(rs), rs))


def test_the_24_scenarios_are_all_defined_exactly_once():
    assert sorted(s["id"] for s in ev.SCENARIOS) == list(range(1, 25)) and all(s["name"] and s["group"] in ("A1", "A2") and callable(s["fn"]) for s in ev.SCENARIOS)
    assert sum(1 for s in ev.SCENARIOS if s["group"] == "A1") == 10


def test_baseline_mode_restores_everything_it_patches():
    from app.tools import registry
    orig = authz.resolve_student_target, registry.REGISTRY["request_sensitive_change"].handler
    with ev.baseline_mode():
        assert authz.resolve_student_target is not orig[0]
    assert (authz.resolve_student_target, registry.REGISTRY["request_sensitive_change"].handler) == orig


def test_the_application_has_no_baseline_switch_of_its_own():
    import pathlib, re
    code_only = lambda t: "\n".join(line.split("#")[0] for line in t.splitlines())                 # comments may mention the rejected field; code must not use it
    src = "\n".join(code_only(p.read_text(encoding="utf-8")) for p in (config.ROOT / "app").rglob("*.py") if p.name != "evaluation_service.py")
    assert not re.search(r"controls_enabled|baseline_mode|DISABLE_AUTH|SKIP_AUTH|BYPASS", src)


def test_full_run_through_the_real_stack_passes_all_24_and_the_baseline_is_measurably_weaker(database, tmp_path):
    report = ev.run_full("mock", "mock-planner-v3")
    m = report["metrics"]
    failed = [s["id"] for s in report["scenarios"] if not s["passed"]]
    assert failed == [] and m["total_test_cases"] == 24 and m["task_success_rate_pct"] == 100.0
    assert m["privacy_blocking_rate_pct"] == 100.0 and m["approval_enforcement_rate_pct"] == 100.0 and report["token_source"].startswith("estimated")
    assert m["input_tokens_provider_reported"] == 0 and m["input_tokens_estimated"] > 0                       # mock tokens are never presented as provider-reported
    c = report["comparison"]
    assert c["privacy_blocking_a1_config"] < c["privacy_blocking_a2_config"] and c["approval_enforcement_a1_config"] < c["approval_enforcement_a2_config"]
    assert c["functional_success_a1_config"] == c["functional_success_a2_config"] == 100.0
    paths = ev.write_reports(report, tmp_path)
    assert json.loads(open(paths["json"]).read())["metrics"]["total_test_cases"] == 24
    rows = list(csv.DictReader(open(paths["csv"])))
    assert len(rows) == 24 and {r["passed"] for r in rows} == {"True"}
    md = open(paths["md"]).read()
    assert "Assignment 1 configuration vs Assignment 2 configuration" in md and "offline mock" in md and "| 24 |" in md


def test_a_crashing_scenario_counts_as_failed_not_skipped(database):
    boom = {"id": 99, "name": "boom", "group": "A2", "fn": lambda c: 1 / 0}
    ev.SCENARIOS.append(boom)
    try:
        from app.main import app
        from app.services.seed_service import DEMO_PASSWORDS
        res = ev.run_scenarios(ev.Env(app, DEMO_PASSWORDS), only=[99])
    finally:
        ev.SCENARIOS.remove(boom)
    assert res[0].passed is False and "ZeroDivisionError" in res[0].error


def test_admin_evaluation_endpoint_serves_only_a_real_saved_run(staff, aarav, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EVAL_RESULTS_DIR", tmp_path)
    j = staff.get("/api/admin/evaluation").json()
    assert j["available"] is False and "run_evaluation" in j["message"]
    (tmp_path / "latest.json").write_text(json.dumps({"provider": "mock", "metrics": {"total_test_cases": 24}, "scenarios": []}))
    assert staff.get("/api/admin/evaluation").json()["metrics"]["total_test_cases"] == 24
    assert aarav.get("/api/admin/evaluation").status_code == 403


def test_run_evaluation_script_refuses_when_groq_is_not_configured():
    import subprocess, sys, os
    env = {**os.environ, "GROQ_API_KEY": "", "GROQ_MODEL": "", "LLM_PROVIDER": "groq"}
    r = subprocess.run([sys.executable, str(config.ROOT / "scripts" / "run_evaluation.py")], capture_output=True, text=True, env=env, cwd=config.ROOT)
    assert r.returncode == 2 and "Cannot run" in r.stderr and "--provider mock" in r.stderr           # never fabricates results or silently substitutes the mock
