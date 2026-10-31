from __future__ import annotations

from typing import Any

from app.schemas import PolicyArgs
from app.services import retrieval_service
from app.tools.registry import ToolContext, register


@register("search_student_policy", "Search the AIDTM Student Handbook / policies / calendars / fee rules. Returns the most relevant entries with ID, section, status and score. "
          "Use for any rule, deadline, amount, procedure or 'can I / how do I' question. Answer ONLY from the returned text; if nothing relevant is returned, say the handbook "
          "does not cover it.", PolicyArgs)
def search_student_policy(ctx: ToolContext, a: PolicyArgs) -> dict[str, Any]:
    results = retrieval_service.search(ctx.db, a.query, top_k=a.limit, programme=ctx.actor.programme, category=a.category)
    ctx.trace.retrieval.extend({"id": r["id"], "title": r["title"], "status": r["status"], "score": r["score"], "section": r["section"]} for r in results)
    if not results:
        return {"query": a.query, "results": [], "note": "No relevant policy text found. The handbook does not cover this; tell the student and point them to the Program Office (9099981586)."}
    return {"query": a.query, "results": results}
