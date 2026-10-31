import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app import config
from app.models import PolicyChunk, PolicyDocument
from app.services import ingest_service, retrieval_service as rs
from app.services.evaluation_service import retrieval_eval


def ids(session, q, **kw):
    return [r["id"] for r in rs.search(session, q, **kw)]


@pytest.mark.parametrize("q,expected", [("What is the late fee per day?", {"F.3.1", "F.3.2"}), ("minimum attendance required", {"D.1.1"}), ("how do I apply for a scholarship", {"G.2.1"}),
                                        ("payment portal failed on due date", {"F.2.2"}), ("Which scholarships exist for women?", {"G.1.5"})])
def test_topic_questions_return_the_right_section(session, q, expected):
    assert expected & set(ids(session, q)[:3])


def test_fee_question_does_not_pull_unrelated_parts(session):
    top = ids(session, "when is the next fee installment due")
    assert top[0] == "F.1.2" and all(i.startswith("F.") for i in top[:2])


@pytest.mark.parametrize("q", ["cafeteria menu today", "price of a Tesla Model 3", "xyzzy plugh", "", "the of and"])
def test_out_of_scope_or_empty_questions_return_nothing(session, q):
    assert rs.search(session, q) == []


def test_category_filter_limits_results_to_that_part(session):
    got = ids(session, "late fee", category="F")
    assert got and all(i.startswith("F.") for i in got)


def test_programme_preference_and_legacy_ids(session):
    assert "F.1.4" in ids(session, "fee structure", programme="PGDM-BDA")[:3]
    assert rs.get_chunk(session, "FEE-010")["id"] == "F.2.2" and rs.get_chunk(session, "f.3.1")["id"] == "F.3.1" and rs.get_chunk(session, "Z.9.9") is None


def test_results_carry_evidence_metadata_and_respect_limits(session):
    res = rs.search(session, "attendance policy", top_k=2)
    assert len(res) <= 2 and all({"id", "title", "section", "status", "score", "text", "document"} <= set(r) for r in res)
    from collections import Counter
    assert max(Counter(r["section"].split()[0] for r in rs.search(session, "fees", top_k=8)).values()) <= 2     # no near-duplicate flooding


def test_stemming_and_synonyms_are_applied_identically_to_both_sides():
    assert rs.stem("requesting") == rs.stem("request") and rs.stem("grades") == "grade" and rs.tokens("fees") == ["fee"]
    assert rs.tokens("retaking the course") == rs.tokens("repeat the course")


# ---------------------------------------------------------------- generic splitter (replaceable handbook)
LONG = "\n".join(["# Fees", *[f"Sentence {i} about fees is here and it is short." for i in range(150)],
                 "<!-- page: 7 -->", "# Hostel", "The hostel opens at six. " * 30, "# Tiny", "Just one line."])


def test_splitter_respects_size_overlap_headings_and_pages():
    ch = ingest_service.split_text(LONG, chunk_size=100, overlap=20)
    fees = [c for c in ch if c["section"] == "Fees"]
    assert len(fees) > 3 and all(len(c["text"].split()) <= 100 for c in ch)
    for a, b in zip(fees, fees[1:]):
        assert set(a["text"].split()[-15:]) & set(b["text"].split()[:30])                  # consecutive chunks overlap
    assert all(c["text"].rstrip().endswith(".") for c in fees[:-1])                        # cut at sentence ends, not mid-sentence
    assert any(c["section"] == "Hostel" and c["page"] == 7 for c in ch)
    tiny = [c for c in ch if c["section"] == "Tiny"]
    assert len(tiny) == 1 and tiny[0]["text"] == "Just one line."


def test_splitter_never_loops_forever_on_degenerate_input():
    assert ingest_service.split_text("word " * 500, chunk_size=50, overlap=49) and ingest_service.split_text("", 100, 10) == []


def test_ingest_is_idempotent_replaceable_and_searchable(session, tmp_path):
    n0 = session.execute(select(PolicyChunk)).scalars().all().__len__()
    assert n0 == 239 and ingest_service.ingest_policy(session)["status"] == "unchanged"
    doc = tmp_path / "other_handbook.md"
    doc.write_text("# Parking\nVisitors may park in lot B for two hours only. A permit costs INR 500 per month.\n# Pets\nPets are not allowed on campus.\n", encoding="utf-8")
    r = ingest_service.ingest_policy(session, doc, name=config.POLICY_DOCUMENT_NAME)        # same logical document, new content -> replaced
    assert r["status"] == "ingested" and r["format"] == "generic_text" and r["chunks"] == 2
    assert session.execute(select(PolicyChunk)).scalars().all().__len__() == 2 and session.execute(select(PolicyDocument)).scalars().all().__len__() == 1
    assert ids(session, "where can visitors park, which lot")[:1] == ["S1.1"] and ids(session, "late fee per day") == []
    assert ingest_service.ingest_policy(session, doc, name=config.POLICY_DOCUMENT_NAME)["status"] == "unchanged"
    assert ingest_service.ingest_policy(session, force=True, name="AIDTM extra copy")["chunks"] == 239


def test_missing_policy_file_is_a_clear_error(session):
    with pytest.raises(FileNotFoundError):
        ingest_service.ingest_policy(session, "does/not/exist.md")


# ---------------------------------------------------------------- measured quality (thresholds sit just below the measured values)
def _cases(name):
    return json.loads((config.ROOT / "eval" / name).read_text(encoding="utf-8"))["cases"]


def test_held_out_benchmark_quality(session):
    r = retrieval_eval(session, _cases("retrieval_heldout.json"))
    assert r["hit_at_3_pct"] >= 85 and r["correct_abstentions"] == r["unanswerable"]


def test_fresh_test_benchmark_quality(session):
    r = retrieval_eval(session, _cases("retrieval_test.json"))
    assert r["hit_at_3_pct"] >= 85 and r["correct_abstentions"] == r["unanswerable"]


def test_legacy_benchmark_still_works_but_is_flagged_non_independent(session):
    cases = _cases("retrieval_legacy.json")
    assert sum(c["verbatim_in_index"] for c in cases) == 18
    assert retrieval_eval(session, cases)["hit_at_3_pct"] >= 95


def test_policy_entry_endpoint(aarav):
    j = aarav.get("/api/policy/chunks/F.3.1").json()
    assert j["id"] == "F.3.1" and "late fee" in j["text"].lower()
    assert aarav.get("/api/policy/chunks/NOPE").status_code == 404
