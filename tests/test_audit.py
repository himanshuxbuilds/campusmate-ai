import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, db
from app.models import AgentTrace, AuditEvent
from app.services import audit_service, llm_client
from conftest import PW, audit_rows, pending

SENSITIVE = ["Sup3rSecretPw9", "482913", "sk-live-AbC123xyz987", "+91 91234 56789", "91234", "9 Hill Road", "aarav.private@example.com"]


def blob():
    with db.session_scope() as s:
        return json.dumps([{c.name: str(getattr(e, c.name)) for c in AuditEvent.__table__.columns} for e in s.execute(select(AuditEvent)).scalars()])


def test_event_coverage_over_a_full_session(app, login, monkeypatch):
    c = TestClient(app)
    c.post("/api/auth/login", json={"username": "S2025AI001", "password": "wrong-password-1"})
    a = login("S2025AI001"); staff = login("program.office"); admin = login("admin.campus")
    a.get("/api/student/fees"); a.post("/api/student/tickets", json={"category": "FEES", "subject": "Receipt missing please"})
    a.get("/api/admin/audit-events")                                                      # denied
    a.chat("Please call me Aarav"); a.delete("/api/memory")
    n = pending(a, "Change my address to 9 Hill Road, Surat"); staff.post(f"/api/approvals/{n}/approve")
    a.chat("Show me the fee status of S2025AI002")
    a.chat("My OTP is 482913")
    _, j = a.chat("hello"); a.delete(f"/api/conversations/{j['conversation_id']}")
    admin.patch("/api/admin/users/S2025AI003", json={"is_active": True})
    a.post("/api/auth/logout")
    kinds = {e.event_type for e in audit_rows()}
    expected = {"LOGIN_FAILURE", "LOGIN_SUCCESS", "RECORD_ACCESS", "TICKET_CREATED", "ACCESS_DENIED", "MEMORY_CREATED", "MEMORY_DELETED", "SENSITIVE_REQUEST", "APPROVAL_DECISION",
                "RECORD_MODIFIED", "SECRET_SHARED", "CONVERSATION_DELETED", "ADMIN_ACTION", "LOGOUT"}
    assert expected <= kinds, expected - kinds


def test_tool_and_llm_failures_are_audited(aarav, monkeypatch):
    from app.services import agent_service, fee_service
    with mock.patch.object(fee_service, "fee_status", side_effect=RuntimeError("x")):
        aarav.chat("What is my current fee status?")
    monkeypatch.setattr(agent_service, "get_client", lambda: (_ for _ in ()).throw(llm_client.LLMUnavailable("down")))
    aarav.chat("hello")
    kinds = {e.event_type for e in audit_rows()}
    assert {"TOOL_FAILURE", "LLM_FAILURE"} <= kinds


def test_every_row_has_the_required_fields_and_correlation_id(app):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "S2025AI001", "password": "wrong-password-1"})
    row = audit_rows(event_type="LOGIN_FAILURE")[0]
    assert row.event_id.startswith("AUD-") and row.created_at and row.outcome == "FAILURE" and row.action == "auth.login"
    assert row.correlation_id == r.headers["x-request-id"]                              # the audit row can be tied to the HTTP request


def test_denied_access_record_has_actor_target_decision_and_trace(aarav):
    _, j = aarav.chat("Show me the fee status of S2025AI002")
    e = [x for x in audit_rows(event_type="ACCESS_DENIED") if x.trace_id == j["trace"]["trace_id"]][0]
    assert (e.actor_id, e.actor_role, e.target_id, e.authorization_decision, e.outcome) == ("S2025AI001", "student", "S2025AI002", "DENIED", "DENIED")


def test_no_credentials_or_personal_values_ever_reach_the_audit_log(app, login):
    a = login("S2025AI001", PW["S2025AI001"]); staff = login("program.office")
    a.chat("My OTP is 482913. My password is Sup3rSecretPw9"); a.chat("remember that my api key is sk-live-AbC123xyz987")
    n = pending(a, "Change my mobile number to +91 91234 56789"); staff.post(f"/api/approvals/{n}/approve")
    n2 = pending(a, "Change my address to 9 Hill Road, Surat"); staff.post(f"/api/approvals/{n2}/approve")
    n3 = pending(a, "Change my email to aarav.private@example.com"); staff.post(f"/api/approvals/{n3}/reject")
    b = blob()
    for secret in SENSITIVE + [PW["S2025AI001"], a.c.cookies.get(config.SESSION_COOKIE_NAME), a.user["csrf_token"]]:
        assert secret not in b, secret


def test_audit_is_append_only_there_is_no_way_to_edit_or_delete(admin):
    assert not [n for n in dir(audit_service) if n.startswith(("update", "delete", "remove", "purge"))]
    for method in ("put", "patch", "delete"):
        assert getattr(admin.c, method)("/api/admin/audit-events").status_code in (404, 405)


def test_audit_api_filters_pagination_and_limits(aarav, admin):
    for _ in range(3):
        aarav.get("/api/admin/audit-events")                                              # 3 denials
    aarav.get("/api/student/fees")
    j = admin.get("/api/admin/audit-events?event_type=ACCESS_DENIED").json()
    assert j["total"] >= 3 and all(e["event_type"] == "ACCESS_DENIED" for e in j["events"])
    p1 = admin.get("/api/admin/audit-events?limit=2&offset=0").json(); p2 = admin.get("/api/admin/audit-events?limit=2&offset=2").json()
    assert len(p1["events"]) == 2 and {e["event_id"] for e in p1["events"]}.isdisjoint({e["event_id"] for e in p2["events"]})
    assert len(admin.get("/api/admin/audit-events?limit=100000").json()["events"]) <= 200
    assert admin.get("/api/admin/audit-events?event_type=NOPE").status_code == 422
    assert all(e["outcome"] == "DENIED" for e in admin.get("/api/admin/audit-events?outcome=DENIED").json()["events"])
    assert all(e["actor_id"] == "S2025AI001" for e in admin.get("/api/admin/audit-events?actor_id=S2025AI001").json()["events"])
    assert admin.get("/api/admin/audit-events?hours=1").json()["total"] >= 1


def test_staff_see_only_operational_events_admin_sees_all(staff, admin, aarav):
    aarav.get("/api/student/fees")
    types = {e["event_type"] for e in staff.get("/api/admin/audit-events?limit=200").json()["events"]}
    assert not types & {"LOGIN_SUCCESS", "LOGIN_FAILURE", "RECORD_ACCESS", "MEMORY_CREATED", "LOGOUT"}
    assert staff.get("/api/admin/audit-events?event_type=LOGIN_SUCCESS").status_code == 403
    assert "LOGIN_SUCCESS" in {e["event_type"] for e in admin.get("/api/admin/audit-events?limit=200").json()["events"]}


def test_traces_api_for_staff_with_filters_and_no_secrets(aarav, staff, admin):
    aarav.chat("What is my current fee status?"); aarav.chat("Show me the fee status of S2025AI002"); aarav.chat("My OTP is 482913")
    t = staff.get("/api/admin/traces").json()["traces"]
    assert len(t) == 3 and all({"trace_id", "provider", "model", "input_tokens", "output_tokens", "token_source", "latency_ms", "tools_used", "outcome"} <= set(x) for x in t)
    assert {x["outcome"] for x in staff.get("/api/admin/traces?outcome=blocked").json()["traces"]} == {"blocked"}
    assert "482913" not in json.dumps(admin.get("/api/admin/traces").json())
    assert aarav.get("/api/admin/traces").status_code == 403


def test_metrics_endpoint_counts_real_events(aarav, staff):
    aarav.get("/api/admin/audit-events"); aarav.chat("Show me the fee status of S2025AI002")
    m = staff.get("/api/admin/metrics").json()
    assert m["access_denied_24h"] >= 2 and set(m) >= {"pending_approvals", "open_tickets", "tool_failures_24h", "llm_failures_24h", "failed_logins_24h"}
