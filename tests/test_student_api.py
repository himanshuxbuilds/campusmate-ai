from conftest import audit_rows


def test_profile_masks_the_id_and_has_no_secrets(aarav):
    j = aarav.get("/api/student/profile").json()
    assert j["student_id_masked"].startswith("S2025") and "*" in j["student_id_masked"] and "S2025AI001" not in str(j)
    assert j["programme"] == "PGDM-AIDS" and j["registered_courses"] and not any("password" in k or "hash" in k for k in j)
    assert audit_rows(event_type="RECORD_ACCESS", action="student.profile.read")


def test_fees_match_the_database_record(aarav):
    j = aarav.get("/api/student/fees").json()
    assert j["total_fee"] == j["amount_paid"] + j["amount_due"] and j["status"] in ("PARTIALLY_PAID", "OVERDUE") and j["installments"]


def test_scholarship_record(aarav):
    j = aarav.get("/api/student/scholarships").json()
    assert j["status"] == "APPROVED" and j["scholarship_name"]


def test_ticket_create_list_get_and_validation(aarav):
    r = aarav.post("/api/student/tickets", json={"category": "LIBRARY", "subject": "Book not returned yet", "description": "I returned it on Monday"})
    assert r.status_code == 201
    t = r.json()["ticket"]
    assert t["ticket_number"].startswith("SR-") and t["status"] == "OPEN" and t["priority"] == "NORMAL"
    assert aarav.get(f"/api/student/tickets/{t['ticket_number']}").json()["ticket"]["subject"] == "Book not returned yet"
    assert any(x["ticket_number"] == t["ticket_number"] for x in aarav.get("/api/student/tickets").json()["tickets"])
    assert aarav.post("/api/student/tickets", json={"category": "NOPE", "subject": "valid subject"}).status_code == 422
    assert aarav.post("/api/student/tickets", json={"category": "FEES", "subject": "ok subject", "priority": "EXTREME"}).status_code == 422
    assert aarav.post("/api/student/tickets", json={"category": "FEES", "subject": "x" * 500}).status_code == 422
    assert aarav.post("/api/student/tickets", json={"category": "FEES", "subject": "ok subject", "student_id": "S2025AI002"}).status_code == 422


def test_duplicate_ticket_within_window_returns_the_existing_one(aarav):
    a = aarav.post("/api/student/tickets", json={"category": "FEES", "subject": "Payment failed twice"}).json()
    b = aarav.post("/api/student/tickets", json={"category": "FEES", "subject": "payment  FAILED twice"}).json()
    assert a["ticket"]["ticket_number"] == b["ticket"]["ticket_number"] and b["duplicate_of_recent_ticket"] is True
