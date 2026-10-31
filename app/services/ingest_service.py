"""Policy ingestion: load -> extract -> split -> store chunk metadata in the database.

Two formats:
  * structured handbook (headings like `### F.3.2 | Title` with Type/Status/Questions lines): chunks are the authored entries;
  * any other Markdown/text document: heading-aware splitter using CHUNK_SIZE words with CHUNK_OVERLAP words of overlap,
    keeping `<!-- page: N -->` markers (or form feeds) as page numbers.
Ingestion is idempotent (same file hash = no change) and replaceable (a different document replaces the old one).
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import config
from app.models import PolicyChunk, PolicyDocument

STRUCTURED_RE = re.compile(r"^### [A-Z](?:\.\d+)+ \| ", re.M)
PAGE_RE = re.compile(r"<!--\s*page:\s*(\d+)\s*-->|\f")


def _load_structured_parser():
    path = config.ROOT / "data" / "kb" / "build_kb_chunks.py"
    spec = importlib.util.spec_from_file_location("kb_build", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def split_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[dict[str, Any]]:
    """Heading-aware splitter. Never splits a sentence in the middle when it can avoid it: window ends are moved to sentence boundaries."""
    size = max(50, int(chunk_size or config.CHUNK_SIZE))
    ov = max(0, min(int(overlap if overlap is not None else config.CHUNK_OVERLAP), size // 2))
    sections: list[tuple[str, int | None, str]] = []              # (heading, page, body)
    heading, page, buf = "Document", None, []
    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append((heading, page, body))
    for line in text.replace("\r", "").split("\n"):
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        pm = PAGE_RE.search(line)
        if pm and pm.group(1):
            page = int(pm.group(1)); continue
        if line.strip() == "\f":
            page = (page or 1) + 1; continue
        if m:
            flush(); buf = []; heading = m.group(2).strip()
        else:
            buf.append(line)
    flush()
    chunks: list[dict[str, Any]] = []
    for si, (h, pg, body) in enumerate(sections, 1):
        words = body.split()
        if len(words) <= size:
            chunks.append({"section": h, "page": pg, "text": body, "si": si, "pi": 1}); continue
        start, part = 0, 1
        while start < len(words):
            end = min(len(words), start + size)
            window = words[start:end]
            if end < len(words):                                   # move the cut back to the last sentence end inside the window
                for k in range(len(window) - 1, int(len(window) * 0.6), -1):
                    if window[k].endswith((".", "!", "?", ":")):
                        window, end = window[: k + 1], start + k + 1
                        break
            chunks.append({"section": h, "page": pg, "text": " ".join(window), "si": si, "pi": part})
            if end >= len(words): break
            start, part = max(end - ov, start + 1), part + 1
    return chunks


def ingest_policy(db: Session, path: str | Path | None = None, name: str | None = None, version: str | None = None, force: bool = False) -> dict[str, Any]:
    src = Path(path or config.POLICY_DOCUMENT_PATH)
    if not src.is_absolute():
        src = config.ROOT / src
    if not src.exists():
        raise FileNotFoundError(f"policy document not found: {src}")
    raw = src.read_text(encoding="utf-8")
    h = hashlib.sha256(raw.encode()).hexdigest()
    name = name or config.POLICY_DOCUMENT_NAME
    existing = db.execute(select(PolicyDocument).where(PolicyDocument.document_name == name)).scalars().first()
    if existing and existing.content_hash == h and not force:
        return {"status": "unchanged", "document": name, "chunks": len(existing.chunks), "hash": h[:12]}
    if existing:
        db.execute(delete(PolicyChunk).where(PolicyChunk.document_id == existing.id))
        db.delete(existing)
        db.flush()
    structured = bool(STRUCTURED_RE.search(raw))
    doc = PolicyDocument(document_name=name, version=version or config.POLICY_DOCUMENT_VERSION,
                         source_type="structured_kb" if structured else "generic_text", source_path=str(src.relative_to(config.ROOT)) if src.is_relative_to(config.ROOT) else str(src),
                         content_hash=h)
    db.add(doc); db.flush()
    n = 0
    if structured:
        rows = _load_structured_parser().parse(raw)
        for i, c in enumerate(rows):
            db.add(PolicyChunk(document_id=doc.id, chunk_id=c["id"], chunk_index=i, section_id=c["section"], section_title=c.get("section_title") or "",
                               page_number=None, title=c["title"], chunk_type=c.get("type", "rule"), status=c["status"], applies_to=c.get("applies_to", ""),
                               source=c.get("source", ""), questions=c.get("questions") or [], keywords=c.get("keywords") or [],
                               legacy_ids=c.get("legacy_ids") or [], chunk_text=c["text"], word_count=len(c["text"].split())))
            n += 1
    else:
        for i, c in enumerate(split_text(raw)):
            db.add(PolicyChunk(document_id=doc.id, chunk_id=f"S{c['si']}.{c['pi']}", chunk_index=i, section_id=f"S{c['si']}", section_title=c["section"],
                               page_number=c["page"], title=c["section"], chunk_type="text", status="OFFICIAL", applies_to="", source=src.name,
                               questions=[], keywords=[], legacy_ids=[], chunk_text=c["text"], word_count=len(c["text"].split())))
            n += 1
    db.flush()
    from app.services import retrieval_service
    retrieval_service.invalidate()
    return {"status": "ingested", "document": name, "format": doc.source_type, "chunks": n, "hash": h[:12]}
