from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db, iso
from app.security.deps import Auth, http_error, student_only
from app.services import memory_service

router = APIRouter(prefix="/api/memory", tags=["memory"])


def _view(m) -> dict:
    return {"id": m.id, "category": m.category, "key": m.memory_key, "value": m.memory_value, "source": m.source, "created_at": iso(m.created_at), "expires_at": iso(m.expires_at)}


@router.get("")
def list_memory(db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    return {"items": [_view(m) for m in memory_service.list_all(db, auth.principal.user_code)]}          # scope is the session identity


@router.delete("/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    if not memory_service.delete_item(db, auth.principal, memory_id):
        raise http_error(404, "MEMORY_NOT_FOUND", "Memory item not found.")                          # same answer for missing and for another student's item
    return {"status": "deleted"}


@router.delete("")
def clear_memory(db: Session = Depends(get_db), auth: Auth = Depends(student_only)):
    n = sum(1 for m in memory_service.list_all(db, auth.principal.user_code) if memory_service.delete_item(db, auth.principal, m.id))
    return {"removed": n}
