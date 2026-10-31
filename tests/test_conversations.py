import pytest
from sqlalchemy import func, select

from app import db
from app.models import Conversation, ConversationMessage
from conftest import audit_rows


def test_chat_creates_and_persists_a_conversation(aarav):
    r, j = aarav.chat("What is my current fee status?")
    cid = j["conversation_id"]
    assert len(cid) == 32
    lst = aarav.get("/api/conversations").json()["conversations"]
    assert [c["id"] for c in lst] == [cid] and lst[0]["title"].startswith("What is my current fee")
    msgs = aarav.get(f"/api/conversations/{cid}/messages").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["meta"]["tools"]


def test_follow_up_messages_stay_in_the_same_conversation(aarav):
    _, j1 = aarav.chat("What is my current fee status?")
    _, j2 = aarav.chat("Thanks", j1["conversation_id"])
    assert j2["conversation_id"] == j1["conversation_id"]
    assert len(aarav.get(f"/api/conversations/{j1['conversation_id']}/messages").json()["messages"]) == 4


def test_create_empty_conversation_and_list_order(aarav):
    c1 = aarav.post("/api/conversations", json={"title": "first"}).json()
    aarav.chat("hello")
    lst = aarav.get("/api/conversations").json()["conversations"]
    assert len(lst) == 2 and {c["id"] for c in lst} >= {c1["id"]}


def test_other_students_cannot_read_post_into_or_delete_my_conversation(aarav, ishita):
    _, j = aarav.chat("What is my current fee status?")
    cid = j["conversation_id"]
    assert ishita.get(f"/api/conversations/{cid}/messages").status_code == 404
    assert ishita.post("/api/chat", json={"message": "hello", "conversation_id": cid}).status_code == 404
    assert ishita.delete(f"/api/conversations/{cid}").status_code == 404
    assert aarav.get(f"/api/conversations/{cid}/messages").status_code == 200
    assert ishita.get("/api/conversations").json()["conversations"] == []


def test_foreign_and_missing_conversation_ids_are_indistinguishable(aarav, ishita):
    _, j = aarav.chat("hello")
    a = ishita.get(f"/api/conversations/{j['conversation_id']}/messages")
    b = ishita.get("/api/conversations/" + "0" * 32 + "/messages")
    assert a.status_code == b.status_code == 404 and a.json() == b.json()


def test_malformed_conversation_ids_are_rejected(aarav):
    assert aarav.post("/api/chat", json={"message": "hi", "conversation_id": "../../etc"}).status_code == 422
    assert aarav.get("/api/conversations/not-a-real-id/messages").status_code == 404


def test_delete_removes_messages_and_is_audited(aarav):
    _, j = aarav.chat("hello")
    cid = j["conversation_id"]
    assert aarav.delete(f"/api/conversations/{cid}").status_code == 200
    with db.session_scope() as s:
        assert s.execute(select(func.count()).select_from(ConversationMessage).where(ConversationMessage.conversation_id == cid)).scalar_one() == 0
        assert s.get(Conversation, cid) is None
    assert audit_rows(event_type="CONVERSATION_DELETED")


def test_messages_are_stored_redacted(aarav, session):
    aarav.chat("My OTP is 654321 and my password is Hunter2Pass")
    stored = " ".join(m.message_content for m in session.execute(select(ConversationMessage)).scalars())
    assert "654321" not in stored and "Hunter2Pass" not in stored and "REDACTED" in stored


def test_failed_first_message_leaves_no_empty_conversation(aarav, monkeypatch):
    from app.services import llm_client
    monkeypatch.setattr(llm_client, "get_client", lambda: (_ for _ in ()).throw(llm_client.LLMTimeout("t")))
    from app.services import agent_service
    monkeypatch.setattr(agent_service, "get_client", llm_client.get_client)
    r, j = aarav.chat("hello")
    assert r.status_code == 504 and aarav.get("/api/conversations").json()["conversations"] == []
