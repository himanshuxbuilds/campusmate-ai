from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.security.deps import Auth, authenticated, http_error
from app.services import retrieval_service

router = APIRouter(prefix="/api/policy", tags=["policy"])


@router.get("/chunks/{chunk_id}")
def chunk(chunk_id: str, db: Session = Depends(get_db), auth: Auth = Depends(authenticated)):
    """The text of one handbook entry (policy text is institutional, not private, so any signed-in user may read it)."""
    c = retrieval_service.get_chunk(db, chunk_id[:32])
    if c is None:
        raise http_error(404, "ENTRY_NOT_FOUND", "That handbook entry does not exist.")
    return c
