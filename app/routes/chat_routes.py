from __future__ import annotations

import json
import queue
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db, iso, session_scope
from app.models import Conversation, ConversationMessage
from app.schemas import ChatBody, ConversationCreate
from app.security.deps import Auth, http_error, student_only
from app.security.ratelimit import limiter
from app.services import agent_service, audit_service

router = APIRouter(prefix="/api", tags=["chat"])


def _result_dict(r: agent_service.AgentResult) -> dict[str, Any]:
    return {"conversation_id": r.conversation_id, "reply": r.reply, "user_message_id": r.user_message_id, "assistant_message_id": r.assistant_message_id,
            "sources": r.sources, "approvals": r.approvals, "tickets": r.tickets, "redactions": r.redactions, "outcome": r.outcome, "trace": r.trace, "context": r.context}


def _chat_limit(db: Session, auth: Auth) -> None:
    ok, retry = limiter.allow(f"chat:{auth.principal.user_code}", config.CHAT_RATE_LIMIT_PER_MIN)
    if not ok:
        audit_service.record(db, "RATE_LIMITED", "chat.rate_limit", "DENIED", actor=auth.principal, reason="chat rate limit", commit=True)
        raise HTTPException(status_code=429, detail={"code": "RATE_LIMITED", "message": "You are sending messages too quickly. Please wait a moment.", "retry_after": retry},
                            headers={"Retry-After": str(retry)})


def _agent_http(e: agent_service.AgentError) -> HTTPException:
    return http_error(e.http_status, e.code, e.message, retryable=e.retryable, trace_id=e.trace_id)


@router.post("/chat")
def chat(body: ChatBody, db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    _chat_limit(db, auth)
    try:
        return _result_dict(agent_service.run_chat(db, auth.principal, body.message, body.conversation_id))
    except agent_service.AgentError as e:
        raise _agent_http(e)


@router.post("/chat/stream")
def chat_stream(body: ChatBody, db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    """Server-sent events: `status` events while the agent works (model call, tool name), then one `final` or `error` event."""
    _chat_limit(db, auth)
    principal, q = auth.principal, queue.Queue()

    def worker() -> None:
        try:
            with session_scope() as wdb:
                try:
                    q.put({"type": "final", "data": _result_dict(agent_service.run_chat(wdb, principal, body.message, body.conversation_id, emit=q.put))})
                except agent_service.AgentError as e:
                    q.put({"type": "error", "error": {"code": e.code, "message": e.message, "retryable": e.retryable, "trace_id": e.trace_id}, "status": e.http_status})
        except Exception:                                                    # noqa: BLE001
            from app.logging_config import logger
            logger.exception("chat stream failed")
            q.put({"type": "error", "error": {"code": "INTERNAL_ERROR", "message": "Something went wrong. Please retry.", "retryable": True}, "status": 500})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while (item := q.get()) is not None:
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/conversations")
def list_conversations(limit: int = 30, offset: int = 0, db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    rows = db.execute(select(Conversation).where(Conversation.user_id == auth.principal.user_code, Conversation.deleted_at.is_(None))
                      .order_by(Conversation.updated_at.desc()).limit(max(1, min(limit, 100))).offset(max(0, offset))).scalars().all()
    return {"conversations": [{"id": c.id, "title": c.title, "created_at": iso(c.created_at), "updated_at": iso(c.updated_at)} for c in rows]}


@router.post("/conversations", status_code=201)
def create_conversation(body: ConversationCreate, db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    import secrets
    from app.security.redaction import redact_secrets
    c = Conversation(id=secrets.token_hex(16), user_id=auth.principal.user_code, title=redact_secrets(body.title or "New conversation")[0][:120])
    db.add(c); db.flush()
    return {"id": c.id, "title": c.title, "created_at": iso(c.created_at), "updated_at": iso(c.updated_at)}


def _owned(db: Session, auth: Auth, conversation_id: str) -> Conversation:
    c = agent_service.get_owned_conversation(db, auth.principal, conversation_id) if len(conversation_id) == 32 else None
    if c is None:
        raise http_error(404, "CONVERSATION_NOT_FOUND", "Conversation not found.")        # identical for missing and for someone else's
    return c


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: str, db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    c = _owned(db, auth, conversation_id)
    msgs = db.execute(select(ConversationMessage).where(ConversationMessage.conversation_id == c.id).order_by(ConversationMessage.id)).scalars().all()
    return {"conversation": {"id": c.id, "title": c.title}, "messages": [{"id": m.id, "role": m.role, "content": m.message_content, "created_at": iso(m.created_at), "trace_id": m.trace_id, "meta": m.meta} for m in msgs]}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    c = _owned(db, auth, conversation_id)
    n = db.execute(select(func.count()).where(ConversationMessage.conversation_id == c.id)).scalar_one()
    db.delete(c)                                                            # cascades to its messages
    audit_service.record(db, "CONVERSATION_DELETED", "conversation.delete", "SUCCESS", actor=auth.principal, target_type="conversation", target_id=conversation_id, details={"messages": n})
    return {"status": "deleted"}
