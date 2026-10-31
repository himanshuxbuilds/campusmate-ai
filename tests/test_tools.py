import json
from datetime import date
from unittest import mock

import pytest
from sqlalchemy import delete, select

from app import db
from app.models import AuditEvent, FeeInstallment, FeeRecord, StudentRecord, SupportTicket
from app.security.context import Principal
from app.services import fee_service
from app.services.trace_service import TraceRecorder
from app.tools import REGISTRY, ToolContext, run_tool, tool_specs

ME = Principal("S2025AI001", "Aarav Mehta", "student", "PGDM-AIDS")


@pytest.fixture()
def ctx(session):
    return ToolContext(session, ME, TraceRecorder(ME.user_code, None, "test", "t"), date(2026, 10, 30))


def run(ctx, name, args=None):
    return run_tool(ctx, name, args or {})


def code(r):
    return r["error"]["code"]


# ---------------------------------------------------------------- registry
def test_registry_has_the_specified_tools_and_only_students_see_them():
    spec_tools = {"get_student_profile", "check_fee_status", "check_scholarship_status", "search_student_policy", "create_support_ticket", "get_ticket_status",
                  "request_sensitive_change", "get_current_academic_calendar"}
    assert spec_tools <= set(REGISTRY)
    assert {t["function"]["name"] for t in tool_specs("student")} == set(REGISTRY)
    assert tool_specs("staff") == [] and tool_specs("admin") == []


def test_tool_schemas_are_clean_for_function_calling():
    for t in tool_specs("student"):
        f = t["function"]; blob = json.dumps(f["parameters"])
        assert f["description"] and f["parameters"]["type"] == "object" and '"title"' not in blob and '"null"' not in blob
        assert f["parameters"].get("additionalProperties") is False


def test_no_tool_takes_code_sql_or_shell():
    assert not any(w in n for n in REGISTRY for w in ("exec", "eval", "sql", "shell", "query_db", "run_"))


# ---------------------------------------------------------------- validation and rejection
@pytest.mark.parametrize("name,args", [("check_fee_status", {"evil": 1}), ("create_support_ticket", {"category": "NOPE", "subject": "valid subject"}),
                                       ("create_support_ticket", {"category": "FEES", "subject": "ok subject", "priority": "EXTREME"}),
                                       ("create_support_ticket", {"category": "FEES", "subject": "x" * 1000}), ("get_ticket_status", {"ticket_number": "'; DROP TABLE users;--"}),
                                       ("compute_late_fee", {"days_late": "many"}), ("compute_late_fee", {}), ("search_student_policy", {"query": "x"}),
                                       ("save_memory", {"key": "general", "value": "x"}), ("request_sensitive_change", {"action_type": "DELETE_EVERYTHING", "changes": {}})])
def test_bad_arguments_are_rejected_with_a_clean_error(ctx, session, name, args):
    before = len(session.execute(select(SupportTicket)).scalars().all())
    r = run(ctx, name, args)
    assert not r["success"] and code(r) == "INVALID_ARGUMENTS"
    assert "Traceback" not in r["error"]["message"] and "tool_" not in r["error"]["message"]
    assert len(session.execute(select(SupportTicket)).scalars().all()) == before           # nothing was created from rejected input


@pytest.mark.parametrize("garbage", ["garbage", None, 42, ["a"]])
def test_non_object_arguments_are_rejected_even_for_all_optional_tools(ctx, garbage):
    r = run_tool(ctx, "check_fee_status", garbage)
    assert not r["success"] and code(r) == "INVALID_ARGUMENTS"


def test_unknown_tool_and_hostile_tool_names(ctx):
    assert code(run(ctx, "drop_database")) == "UNKNOWN_TOOL"
    assert code(run_tool(ctx, "<img src=x onerror=1>", {})) == "UNKNOWN_TOOL"
    assert ctx.trace.tools_used[-1]["name"] == "?img?src?x?onerror?1?"             # hostile tool names are neutralised before they reach traces/UI


def test_role_restriction_is_enforced_by_the_executor(session):
    staff = Principal("program.office", "PO", "staff", None, {})
    c = ToolContext(session, staff, TraceRecorder("program.office", None, "t", "t"), date(2026, 10, 30))
    r = run(c, "check_fee_status")
    assert code(r) == "FORBIDDEN" and any(e.event_type == "ACCESS_DENIED" for e in session.execute(select(AuditEvent)).scalars())


# ---------------------------------------------------------------- data tools
def test_fee_status_reflects_the_database(ctx, session):
    r = run(ctx, "check_fee_status")["data"]
    rec = session.execute(select(FeeRecord).where(FeeRecord.student_id == "S2025AI001")).scalar_one()
    assert r["amount_paid"] == rec.amount_paid and r["amount_due"] == rec.amount_due and r["total_fee"] == rec.total_fee == rec.amount_paid + rec.amount_due
    assert r["next_due"]["name"] == "4th installment" and r["status"] == "PARTIALLY_PAID"


def test_overdue_installment_accrues_late_fee_from_the_policy(ctx, session):
    inst = session.execute(select(FeeInstallment).where(FeeInstallment.student_id == "S2025AI001", FeeInstallment.status == "PENDING")).scalars().first()
    inst.due_date = date(2026, 10, 10); session.flush()                            # 20 days before the frozen 'today'
    r = run(ctx, "check_fee_status")["data"]
    row = next(i for i in r["installments"] if i["name"] == inst.name)
    assert row["status"] == "OVERDUE" and row["days_overdue"] == 20 and row["late_fee_accrued_as_of_today"] == 14 * 100 + 6 * 200 and r["status"] == "OVERDUE"


def test_missing_fee_record_is_reported_not_invented(ctx, session):
    session.execute(delete(FeeInstallment).where(FeeInstallment.student_id == "S2025AI001")); session.execute(delete(FeeRecord).where(FeeRecord.student_id == "S2025AI001")); session.flush()
    r = run(ctx, "check_fee_status")
    assert code(r) == "FEE_RECORD_UNAVAILABLE" and "KeyError" not in r["error"]["message"] and "fees" not in r["error"]["message"].lower().split("fee record")[0]


def test_scholarship_profile_and_calendar(ctx):
    assert run(ctx, "check_scholarship_status")["data"]["status"] == "APPROVED"
    p = run(ctx, "get_student_profile")["data"]
    assert p["student_id"] == "S2025AI001" and "password" not in json.dumps(p) and p["registered_courses"]
    cal = run(ctx, "get_current_academic_calendar", {"query": "exam"})["data"]
    assert cal["events"] and all(e["type"] == "exam" for e in cal["events"])
    far = run(ctx, "get_current_academic_calendar", {"from_date": "2040-01-01", "query": "exam"})["data"]
    assert far["events"] == [] and "No verified date" in far["note"]


def test_curriculum_and_late_fee_calculator(ctx):
    t4 = run(ctx, "get_curriculum", {"trimester": "IV"})["data"]
    assert len(t4["trimesters"]) == 1 and t4["trimesters"][0]["courses"]
    assert run(ctx, "compute_late_fee", {"days_late": 20})["data"]["total_late_fee"] == 2600
    assert run(ctx, "compute_late_fee", {"days_late": 60})["data"]["days_beyond_6_weeks"] == 18
    assert code(run(ctx, "compute_late_fee", {"due_date": "2026-10-30", "payment_date": "2026-10-01"})) == "NOT_LATE"


def test_policy_search_logs_evidence_and_admits_gaps(ctx):
    r = run(ctx, "search_student_policy", {"query": "payment portal failed on the due date"})["data"]
    assert r["results"][0]["id"] == "F.2.2" and r["results"][0]["score"] > 0 and ctx.trace.retrieval[0]["id"] == "F.2.2"
    gap = run(ctx, "search_student_policy", {"query": "cafeteria menu today"})["data"]
    assert gap["results"] == [] and "does not cover" in gap["note"]


# ---------------------------------------------------------------- tickets
def test_ticket_creation_is_database_backed_and_idempotent(ctx, session):
    r = run(ctx, "create_support_ticket", {"category": "FEES", "subject": "Payment failed", "description": "portal error", "priority": "HIGH"})["data"]
    t = session.execute(select(SupportTicket).where(SupportTicket.ticket_number == r["ticket"]["ticket_number"])).scalar_one()
    assert t.student_id == "S2025AI001" and t.priority == "HIGH" and t.status == "OPEN" and r["duplicate_of_recent_ticket"] is False
    again = run(ctx, "create_support_ticket", {"category": "FEES", "subject": "payment  failed"})["data"]
    assert again["duplicate_of_recent_ticket"] is True and again["ticket"]["ticket_number"] == t.ticket_number
    assert session.execute(select(SupportTicket).where(SupportTicket.student_id == "S2025AI001", SupportTicket.subject.ilike("%payment failed%"))).scalars().all().__len__() == 1


def test_ticket_numbers_are_unique_and_increasing(ctx):
    nums = [run(ctx, "create_support_ticket", {"category": "OTHER", "subject": f"Unique request number {i}"})["data"]["ticket"]["ticket_number"] for i in range(3)]
    assert len(set(nums)) == 3 and nums == sorted(nums)


def test_ticket_status_owner_only_and_uniform_not_found(ctx, session):
    other = session.execute(select(SupportTicket).where(SupportTicket.student_id != "S2025AI001")).scalars().first()
    a = run(ctx, "get_ticket_status", {"ticket_number": other.ticket_number}); b = run(ctx, "get_ticket_status", {"ticket_number": "SR-2099-0001"})
    assert code(a) == code(b) == "TICKET_NOT_FOUND" and a["error"]["message"] == b["error"]["message"]
    mine = run(ctx, "create_support_ticket", {"category": "OTHER", "subject": "My own ticket here"})["data"]["ticket"]["ticket_number"]
    assert run(ctx, "get_ticket_status", {"ticket_number": mine})["data"]["ticket"]["status"] == "OPEN"


# ---------------------------------------------------------------- sensitive change tool
def test_sensitive_change_files_a_request_and_never_applies_it(ctx, session):
    before = session.execute(select(StudentRecord.mobile).where(StudentRecord.student_id == "S2025AI001")).scalar_one()
    r = run(ctx, "request_sensitive_change", {"action_type": "UPDATE_CONTACT", "changes": {"mobile": "+91 99887 76655"}, "reason": "new SIM"})["data"]
    assert r["status"] == "PENDING_APPROVAL" and r["applied"] is False and r["request_number"].startswith("APR-")
    assert session.execute(select(StudentRecord.mobile).where(StudentRecord.student_id == "S2025AI001")).scalar_one() == before


@pytest.mark.parametrize("args,expected", [({"action_type": "UPDATE_CONTACT", "changes": {"mobile": "12"}}, "INVALID_CHANGE"), ({"action_type": "UPDATE_CONTACT", "changes": {"email": "not-an-email"}}, "INVALID_CHANGE"),
                                           ({"action_type": "UPDATE_CONTACT", "changes": {}}, "INVALID_CHANGE"), ({"action_type": "UPDATE_REFUND_BANK", "changes": {"bank_name": "HDFC", "account_last4": "12345678"}}, "INVALID_CHANGE"),
                                           ({"action_type": "CHANGE_COURSE", "changes": {"from_course": "Python for Data Science", "to_course": "HR Analytics"}}, "COURSE_NOT_REGISTERED"),
                                           ({"action_type": "CHANGE_COURSE", "changes": {"from_course": "Entrepreneurship Essentials", "to_course": "Underwater Basket Weaving"}}, "NOT_IN_BASKET"),
                                           ({"action_type": "SWITCH_PROGRAMME", "changes": {"target_programme": "PGDM-AIDS"}}, "NO_CHANGE"), ({"action_type": "FEE_CORRECTION", "changes": {"installment_name": "99th installment", "new_late_fee": 0}}, "INSTALLMENT_NOT_FOUND"),
                                           ({"action_type": "UPDATE_CONTACT", "changes": {"mobile": "+91 98765 43210", "is_admin": True}}, "INVALID_CHANGE")])
def test_sensitive_change_validation(ctx, args, expected):
    r = run(ctx, "request_sensitive_change", args)
    assert not r["success"] and code(r) == expected


def test_core_courses_cannot_be_swapped(ctx):
    r = run(ctx, "request_sensitive_change", {"action_type": "CHANGE_COURSE", "changes": {"from_course": "Deep Learning", "to_course": "HR Analytics"}})
    assert code(r) in ("COURSE_NOT_REGISTERED", "CORE_COURSE")


# ---------------------------------------------------------------- failure handling and log hygiene
def test_unexpected_failure_is_generic_audited_and_leaks_nothing(ctx, session):
    with mock.patch.object(fee_service, "fee_status", side_effect=RuntimeError("db down: postgresql://admin:Sup3rS3cret@10.0.0.5/campus")):
        r = run(ctx, "check_fee_status")
    blob = json.dumps(r)
    assert code(r) == "TOOL_FAILED" and "Sup3rS3cret" not in blob and "postgresql" not in blob and r["error"]["reference"].startswith("REQ-")
    ev = session.execute(select(AuditEvent).where(AuditEvent.event_type == "TOOL_FAILURE")).scalars().all()
    assert ev and "Sup3rS3cret" not in json.dumps([(e.reason, e.details) for e in ev])


def test_trace_arguments_are_redacted(ctx):
    run(ctx, "request_sensitive_change", {"action_type": "UPDATE_CONTACT", "changes": {"mobile": "+91 99887 76655"}})
    run(ctx, "create_support_ticket", {"category": "OTHER", "subject": "my otp is 998877 please help"})
    dump = json.dumps(ctx.trace.tools_used)
    assert "99887 76655" not in dump and "998877" not in dump


def test_failed_tool_leaves_no_partial_change(ctx, session):
    from app.services import audit_service
    real = audit_service.record
    def boom_for_tickets(db_, event_type, *a, **k):
        if event_type == "TICKET_CREATED":
            raise RuntimeError("boom")
        return real(db_, event_type, *a, **k)
    n = len(session.execute(select(SupportTicket)).scalars().all())
    with mock.patch.object(audit_service, "record", side_effect=boom_for_tickets):
        r = run(ctx, "create_support_ticket", {"category": "OTHER", "subject": "Rolled back ticket"})
    assert code(r) == "TOOL_FAILED" and len(session.execute(select(SupportTicket)).scalars().all()) == n


def test_failure_handler_survives_a_failing_audit_write(ctx):
    with mock.patch.object(fee_service, "fee_status", side_effect=RuntimeError("x")), mock.patch("app.tools.registry.audit_service.record", side_effect=RuntimeError("audit down")):
        r = run(ctx, "check_fee_status")
    assert code(r) == "TOOL_FAILED"                                                 # still a clean envelope, not a crash
