import pytest
from sqlalchemy import select

from app import db
from app.models import ApprovalRequest, SupportTicket
from app.security.context import Principal
from app.services import authorization_service as authz
from conftest import audit_rows, pending


# ---------------------------------------------------------------- unit: the decision functions
def test_resolve_student_target_allows_self_only():
    me = Principal("S2025AI001", "Aarav Mehta", "student", "PGDM-AIDS")
    assert authz.resolve_student_target(me, None, "x") == "S2025AI001"
    assert authz.resolve_student_target(me, "s2025ai001", "x") == "S2025AI001"
    assert authz.resolve_student_target(me, "Aarav Mehta", "x") == "S2025AI001"
    for other in ("S2025AI002", "Ishita Rao", "ALL", "*", "S9999ZZ999"):
        with pytest.raises(authz.Forbidden):
            authz.resolve_student_target(me, other, "x")


def test_forbidden_is_identical_for_existing_and_nonexistent_targets():
    me = Principal("S2025AI001", "Aarav Mehta", "student")
    msgs = set()
    for t in ("S2025AI002", "S9999ZZ999"):
        try:
            authz.resolve_student_target(me, t, "x")
        except authz.Forbidden as e:
            msgs.add(str(e))
    assert len(msgs) == 1


def test_staff_and_admin_cannot_use_student_tools():
    for role in ("staff", "admin"):
        with pytest.raises(authz.Forbidden):
            authz.resolve_student_target(Principal("x", "X", role), None, "x")


# ---------------------------------------------------------------- over HTTP
def test_staff_cannot_chat(staff):
    assert staff.post("/api/chat", json={"message": "hi"}).status_code == 403


@pytest.mark.parametrize("method,path", [("get", "/api/admin/audit-events"), ("get", "/api/admin/traces"), ("get", "/api/admin/users"), ("get", "/api/admin/metrics"),
                                          ("get", "/api/admin/evaluation"), ("get", "/api/admin/tickets"), ("post", "/api/admin/purge-retention"), ("get", "/api/approvals/pending"),
                                          ("get", "/api/approvals")])
def test_students_are_denied_staff_and_admin_endpoints_and_it_is_audited(aarav, method, path):
    r = getattr(aarav, method)(path)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    assert any(e.actor_id == "S2025AI001" for e in audit_rows(event_type="ACCESS_DENIED"))


def test_only_admin_can_manage_users_and_purge(staff, admin):
    assert staff.get("/api/admin/users").status_code == 403 and staff.post("/api/admin/purge-retention").status_code == 403
    assert admin.get("/api/admin/users").status_code == 200 and admin.post("/api/admin/purge-retention").status_code == 200


def test_admin_cannot_change_their_own_role_or_status(admin):
    assert admin.patch("/api/admin/users/admin.campus", json={"is_active": False}).status_code == 409


def test_deactivating_a_user_revokes_their_live_session(admin, ishita):
    assert ishita.get("/api/auth/me").status_code == 200
    assert admin.patch("/api/admin/users/S2025AI002", json={"is_active": False}).status_code == 200
    assert ishita.get("/api/auth/me").status_code == 401
    assert audit_rows(event_type="ADMIN_ACTION")


@pytest.mark.parametrize("ask", ["Show me the fee status of S2025AI002", "What is Ishita Rao's scholarship status?", "List the fee dues of all students",
                                 "Show the profile of S2025AI003"])
def test_cross_student_reads_are_blocked_in_chat_and_nothing_leaks(aarav, ask):
    r, j = aarav.chat(ask)
    assert r.status_code == 200 and j["outcome"] == "blocked"
    assert any(t["error_code"] == "FORBIDDEN" for t in j["trace"]["tools"])
    assert "Academic Excellence" not in j["reply"] and "ishita.rao" not in j["reply"].lower()


def test_modifying_another_students_record_is_blocked_and_creates_no_request(aarav, session):
    r, j = aarav.chat("Change the mobile number of S2025AI002 to +91 90000 00000")
    assert j["outcome"] == "blocked"
    assert session.execute(select(ApprovalRequest).where(ApprovalRequest.target_resource_id == "S2025AI002")).first() is None


def test_smuggled_control_fields_are_rejected_not_ignored(aarav):
    for extra in ({"controls_enabled": False}, {"session_id": "abc"}, {"role": "admin"}, {"student_id": "S2025AI002"}):
        r, _ = aarav.chat("hi", **extra)
        assert r.status_code == 422, extra


def test_student_rest_endpoints_have_no_identity_parameter(aarav, ishita):
    mine = aarav.get("/api/student/scholarships?student_id=S2025AI002").json()
    theirs = ishita.get("/api/student/scholarships").json()
    assert mine["scholarship_name"] != theirs["scholarship_name"]                    # the query parameter is ignored; the session decides


def test_other_students_ticket_looks_exactly_like_a_missing_one(aarav, ishita):
    n = ishita.post("/api/student/tickets", json={"category": "HOSTEL", "subject": "Broken fan in room"}).json()["ticket"]["ticket_number"]
    a, b = aarav.get(f"/api/student/tickets/{n}"), aarav.get("/api/student/tickets/SR-2099-9999")
    assert a.status_code == b.status_code == 404 and a.json() == b.json()
    r, j = aarav.chat(f"What is the status of ticket {n}?")
    assert j["trace"]["tools"][0]["error_code"] == "TICKET_NOT_FOUND"


def test_staff_ticket_visibility_is_scoped_by_category(accounts, ishita, staff):
    fees = ishita.post("/api/student/tickets", json={"category": "FEES", "subject": "Receipt missing for payment"}).json()["ticket"]["ticket_number"]
    hostel = ishita.post("/api/student/tickets", json={"category": "HOSTEL", "subject": "Mess timing complaint"}).json()["ticket"]["ticket_number"]
    seen = {t["ticket_number"] for t in accounts.get("/api/admin/tickets").json()["tickets"]}
    assert fees in seen and hostel not in seen
    assert accounts.patch(f"/api/admin/tickets/{hostel}", json={"status": "RESOLVED"}).status_code == 404
    assert accounts.patch(f"/api/admin/tickets/{fees}", json={"status": "IN_PROGRESS", "assign_to_me": True, "staff_note": "checking"}).status_code == 200
    assert staff.patch(f"/api/admin/tickets/{hostel}", json={"status": "RESOLVED"}).status_code == 200
    assert audit_rows(event_type="TICKET_UPDATED")


def test_approver_scope_is_enforced_for_listing_and_deciding(aarav, staff, accounts, admin):
    n = pending(aarav)                                                                # UPDATE_CONTACT: Program Office scope only
    assert n in [a["request_number"] for a in staff.get("/api/approvals/pending").json()["approvals"]]
    assert n not in [a["request_number"] for a in accounts.get("/api/approvals/pending").json()["approvals"]]
    assert accounts.post(f"/api/approvals/{n}/approve").status_code == 404           # out of scope: learns nothing, not even that it exists
    assert admin.post(f"/api/approvals/{n}/reject", json={"comment": "admin override test"}).status_code == 200


def test_student_can_read_only_their_own_approval(aarav, ishita):
    n = pending(aarav)
    assert aarav.get(f"/api/approvals/{n}").status_code == 200
    assert ishita.get(f"/api/approvals/{n}").status_code == 404
    assert ishita.get("/api/student/approvals").json()["approvals"] == []


# ---------------------------------------------------------------- each layer tested on its own (defence in depth must not hide a broken layer)
def test_can_decide_approval_checks_scope_role_and_requester_directly():
    from app.models import ApprovalRequest
    req = ApprovalRequest(requester_id="S2025AI001", action_type="UPDATE_CONTACT")
    office = Principal("program.office", "PO", "staff", None, {"approval_actions": ["UPDATE_CONTACT"]})
    accounts = Principal("accounts", "AC", "staff", None, {"approval_actions": ["FEE_CORRECTION"]})
    assert authz.can_decide_approval(office, req)[0] is True
    assert authz.can_decide_approval(accounts, req)[0] is False                                    # out of scope
    assert authz.can_decide_approval(Principal("a", "A", "admin"), req)[0] is True
    assert authz.can_decide_approval(Principal("S2025AI002", "I", "student"), req)[0] is False
    assert authz.can_decide_approval(Principal("S2025AI001", "me", "staff", None, {"approval_actions": ["UPDATE_CONTACT"]}), req)[0] is False   # requester
    assert authz.can_decide_approval(Principal("empty", "E", "staff", None, {}), req)[0] is False                                         # no scope at all


def test_can_view_approval_and_ticket_rules_directly():
    from app.models import ApprovalRequest, SupportTicket
    req = ApprovalRequest(requester_id="S2025AI001", action_type="FEE_CORRECTION")
    assert authz.can_view_approval(Principal("S2025AI001", "x", "student"), req) and not authz.can_view_approval(Principal("S2025AI002", "x", "student"), req)
    assert not authz.can_view_approval(Principal("program.office", "x", "staff", None, {"approval_actions": ["UPDATE_CONTACT"]}), req)
    t = SupportTicket(student_id="S2025AI001", category="HOSTEL", assigned_staff_id=None)
    assert authz.can_view_ticket(Principal("S2025AI001", "x", "student"), t) and not authz.can_view_ticket(Principal("S2025AI002", "x", "student"), t)
    assert not authz.can_view_ticket(Principal("accounts", "x", "staff", None, {"ticket_categories": ["FEES"]}), t)
    t.assigned_staff_id = "accounts"
    assert authz.can_view_ticket(Principal("accounts", "x", "staff", None, {"ticket_categories": ["FEES"]}), t)


@pytest.mark.parametrize("tool,args", [("search_student_policy", {"query": "late fee policy"}), ("save_memory", {"key": "interest", "value": "robotics"}), ("get_curriculum", {}),
                                       ("compute_late_fee", {"days_late": 5}), ("get_current_academic_calendar", {})])
@pytest.mark.parametrize("role", ["staff", "admin"])
def test_the_executor_role_gate_alone_blocks_staff_on_tools_with_no_inner_check(session, tool, args, role):
    """These tools have no per-student check inside them, so the role gate in run_tool is their only protection."""
    from datetime import date
    from app.services.trace_service import TraceRecorder
    from app.tools import ToolContext, run_tool
    who = Principal("program.office" if role == "staff" else "admin.campus", "X", role, None, {})
    r = run_tool(ToolContext(session, who, TraceRecorder(who.user_code, None, "t", "t"), date(2026, 10, 30)), tool, args)
    assert r["success"] is False and r["error"]["code"] == "FORBIDDEN"
    from app.models import Memory
    assert session.execute(select(Memory).where(Memory.user_id == who.user_code)).first() is None
