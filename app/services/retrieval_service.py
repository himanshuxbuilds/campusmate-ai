"""Policy retrieval (RAG) over the chunks stored in the database.

Scoring = BM25 over text + boosted title/keywords/questions + question-match bonus, with a coverage filter
(drops chunks that match only one incidental word), a score threshold, a preference for the student's own
programme, overview chunks for broad questions, and at most `per_section` chunks per section so the top-k is
not filled with near-duplicates. Only the top-k chunks reach the model.
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import PolicyChunk, PolicyDocument

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = set("""the a an of to in for and or is are be on at by with my me i you your it this that what how when can do does if will from as into about
please tell give show list all any get got am was were there their them they we our us have has had which who whom where why should would could
some more most much many also than then so just only very explain everything complete full entire whole detail detailed details know need want
current currently today now latest right""".split())
_SYN = {
    "fees": "fee", "installments": "installment", "instalment": "installment", "instalments": "installment", "scholarships": "scholarship",
    "exams": "exam", "examination": "exam", "examinations": "exam", "holidays": "holiday", "vacation": "holiday", "hostels": "hostel",
    "penalty": "fine", "penalties": "fine", "fines": "fine", "placements": "placement", "internships": "internship", "sip": "internship",
    "ppo": "offer", "attend": "attendance", "attending": "attendance", "deadlines": "deadline", "pay": "payment", "paid": "payment",
    "paying": "payment", "payments": "payment", "timings": "hours", "timing": "hours", "subjects": "course", "subject": "course",
    "courses": "course", "syllabus": "course", "papers": "course", "semester": "trimester", "semesters": "trimester", "sem": "trimester",
    "term": "trimester", "terms": "trimester", "trim": "trimester", "trimesters": "trimester", "trisem": "trimester", "marks": "grade",
    "grades": "grade", "result": "grade", "results": "grade", "cheat": "unfair", "cheating": "unfair", "copying": "unfair", "books": "book",
    "refunds": "refund", "withdraw": "withdrawal", "withdrawing": "withdrawal", "food": "mess", "meals": "mess", "rooms": "hostel",
    "room": "hostel", "bus": "transport", "shuttle": "transport", "doctor": "medical", "hospital": "medical", "hospitals": "medical",
    "ambulance": "medical", "sick": "medical", "illness": "medical", "complaint": "grievance", "complain": "grievance",
    "complaints": "grievance", "ticket": "support", "tickets": "support", "failed": "failure", "fail": "failure", "fails": "failure",
    "failing": "failure", "phone": "mobile", "phones": "mobile", "teaches": "teach", "teaching": "teach", "taught": "teach", "units": "unit",
    "topics": "unit", "topic": "unit", "modules": "module", "loans": "loan", "receipts": "receipt", "certificates": "certificate",
    "documents": "document", "rules": "rule", "policies": "policy", "dates": "date", "ends": "end", "ending": "end", "over": "end",
    "starts": "start", "begin": "start", "begins": "start", "passwords": "password",
    "retake": "repeat", "retaking": "repeat", "reattempt": "repeat", "resit": "repeat", "forbidden": "prohibited", "banned": "prohibited", "barred": "prohibited",
    "recheck": "appeal", "rechecked": "appeal", "revaluation": "appeal", "reevaluation": "appeal", "regrade": "appeal", "dispute": "appeal", "erroring": "failure",
    "error": "failure", "errors": "failure", "glitch": "failure", "website": "portal", "respond": "response", "reply": "response", "turnaround": "response",
    "look": "see", "view": "see", "must": "required", "bring": "required", "absent": "absence", "leave": "absence", "migrate": "switch", "convert": "equivalence",
    "converted": "equivalence", "percentage": "equivalence", "curfew": "hostel", "visitors": "visitor", "letter": "certificate", "bonafide": "certificate",
}
_ROMAN = {"1": "i", "2": "ii", "3": "iii", "4": "iv", "5": "v", "6": "vi"}
_BROAD = re.compile(r"\b(everything|all|complete|full|entire|whole|guide|explain|overview|summary|in detail|detailed|tell me about)\b", re.I)


def stem(t: str) -> str:
    """Very light suffix stripping (requesting->request, retaking->retak, grades->grade). Applied identically to documents and queries."""
    if len(t) <= 4 or t.isdigit():
        return t
    if t.endswith("ies") and len(t) > 5: return t[:-3] + "y"
    if t.endswith("sses"): return t[:-2]
    if t.endswith("ing") and len(t) > 5:
        b = t[:-3]
        return b[:-1] if len(b) > 3 and b[-1] == b[-2] and b[-1] not in "aeiouls" else b
    if t.endswith("ed") and len(t) > 5: return t[:-2]
    if t.endswith("es") and len(t) > 5 and t[-3] in "sxz": return t[:-2]
    if t.endswith("s") and not t.endswith(("ss", "us", "is")): return t[:-1]
    return t


def tokens(text: str) -> list[str]:
    t0 = (text or "").lower()
    for k in ("artificial intelligence & data science", "artificial intelligence and data science", "ai and data science", "ai & ds", "ai&ds", "ai/ds", "ai ds", "ai-ds"):
        t0 = t0.replace(k, " aids ")
    t0 = t0.replace("big data analytics", " bda ")
    out = []
    for t in _TOKEN.findall(t0):
        if t in _STOP:
            continue
        out.append(stem(_SYN.get(_ROMAN.get(t, t), _ROMAN.get(t, t))))
    return out


class KnowledgeIndex:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.by_id: dict[str, dict[str, Any]] = {}
        for r in rows:
            self.by_id[r["chunk_id"].upper()] = r
            for old in r.get("legacy_ids") or []:
                self.by_id.setdefault(old.upper(), r)
        self._docs, self._qsets = [], []
        for r in rows:
            t = tokens(r["chunk_text"]) + tokens(r["title"]) * 3 + tokens(" ".join(r.get("keywords") or [])) * 2
            t += tokens(" ".join(r.get("questions") or [])) * 2 + tokens(r.get("section_title") or "")
            self._docs.append(t)
            self._qsets.append([set(tokens(q)) for q in (r.get("questions") or []) if tokens(q)])
        self._df: Counter = Counter()
        for d in self._docs:
            self._df.update(set(d))
        self._avgdl = sum(len(d) for d in self._docs) / max(1, len(self._docs))
        self._tf = [Counter(d) for d in self._docs]
        self.k1, self.b = 1.4, 0.72

    def _idf(self, term: str) -> float:
        n, df = len(self._docs), self._df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def _qbonus(self, i: int, qset: set[str]) -> float:
        best = 0.0
        for qs in self._qsets[i]:
            inter = qset & qs
            if inter:
                wu = sum(self._idf(t) for t in qset | qs)
                best = max(best, sum(self._idf(t) for t in inter) / wu if wu else 0.0)
        return best

    def search(self, query: str, top_k: int, programme: str | None, category: str | None, min_score: float, per_section: int = 2) -> list[dict[str, Any]]:
        qset = set(tokens(query))
        if not qset:
            return []
        broad = bool(_BROAD.search(query))
        other = {"PGDM-AIDS": "PGDM-BDA", "PGDM-BDA": "PGDM-AIDS"}.get(programme or "")
        mentions_other = bool(other) and (("bda" in qset and other == "PGDM-BDA") or ("aids" in qset and other == "PGDM-AIDS"))
        cat = (category or "").strip().lower()
        q_idf = sum(self._idf(t) for t in qset)
        # Abstain when most of the question's weight sits on words that appear NOWHERE in the handbook (out-of-scope question):
        # showing loosely related policy text would be worse than saying "not covered".
        oov = sum(self._idf(t) for t in qset if self._df.get(t, 0) == 0)
        if q_idf and oov / q_idf > config.RETRIEVAL_OOV_RATIO:
            return []
        scored = []
        for i, r in enumerate(self.rows):
            if cat and not (r["chunk_id"].lower().startswith(cat + ".") or r["chunk_id"].lower() == cat or (r.get("chunk_type") or "").lower() == cat
                            or cat in (r.get("section_title") or "").lower()):
                continue
            tf, dl = self._tf[i], len(self._docs[i])
            s = sum(self._idf(t) * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self._avgdl)) for t in qset if t in tf)
            if s <= 0:
                continue
            if q_idf and sum(self._idf(t) for t in qset if t in tf) / q_idf < (0.5 if len(qset) <= 3 else 0.34):
                continue
            s += 12.0 * self._qbonus(i, qset)
            applies = r.get("applies_to") or ""
            if programme and other and not mentions_other:
                if programme in applies and other not in applies: s *= 1.25
                elif other in applies and programme not in applies: s *= 0.75
            if broad and r.get("chunk_type") == "overview": s *= 1.5
            scored.append((s, r))
        scored.sort(key=lambda x: -x[0])
        out, per = [], Counter()
        for s, r in scored:
            if len(out) >= top_k or s < min_score: break
            if per[r["section_id"]] >= per_section: continue
            per[r["section_id"]] += 1
            txt = r["chunk_text"]
            out.append({"id": r["chunk_id"], "title": r["title"], "type": r.get("chunk_type"), "status": r["status"], "source": r["source"],
                        "section": f"{r['section_id']} {r.get('section_title') or ''}".strip(), "page_number": r.get("page_number"),
                        "document": r.get("document_name"), "score": round(s, 2),
                        "text": txt[: config.CHUNK_CONTEXT_CHARS] + ("…" if len(txt) > config.CHUNK_CONTEXT_CHARS else "")})
        return out


_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "index": None}


def invalidate() -> None:
    with _lock:
        _cache["key"] = None


def _rows(db: Session) -> tuple[Any, list[dict[str, Any]]]:
    docs = db.execute(select(PolicyDocument)).scalars().all()
    key = (str(db.get_bind().url), tuple((d.id, d.content_hash) for d in docs))
    rows = []
    for d in docs:
        for c in db.execute(select(PolicyChunk).where(PolicyChunk.document_id == d.id).order_by(PolicyChunk.chunk_index)).scalars():
            rows.append({"chunk_id": c.chunk_id, "title": c.title, "chunk_type": c.chunk_type, "status": c.status, "applies_to": c.applies_to, "source": c.source,
                         "section_id": c.section_id, "section_title": c.section_title, "page_number": c.page_number, "questions": c.questions,
                         "keywords": c.keywords, "legacy_ids": c.legacy_ids, "chunk_text": c.chunk_text, "document_name": d.document_name})
    return key, rows


def index(db: Session) -> KnowledgeIndex:
    docs = db.execute(select(PolicyDocument.id, PolicyDocument.content_hash)).all()
    key = (str(db.get_bind().url), tuple((i, h) for i, h in docs))
    with _lock:
        if _cache["key"] != key or _cache["index"] is None:
            _, rows = _rows(db)
            _cache["index"], _cache["key"] = KnowledgeIndex(rows), key
        return _cache["index"]


def search(db: Session, query: str, *, top_k: int | None = None, programme: str | None = None, category: str | None = None,
           min_score: float | None = None) -> list[dict[str, Any]]:
    return index(db).search(query, top_k or config.RETRIEVAL_TOP_K, programme, category,
                            config.RETRIEVAL_MIN_SCORE if min_score is None else min_score)


def get_chunk(db: Session, chunk_id: str) -> dict[str, Any] | None:
    r = index(db).by_id.get((chunk_id or "").upper())
    if not r:
        return None
    return {"id": r["chunk_id"], "title": r["title"], "type": r.get("chunk_type"), "status": r["status"], "source": r["source"],
            "section": f"{r['section_id']} {r.get('section_title') or ''}".strip(), "text": r["chunk_text"], "document": r.get("document_name")}
