from __future__ import annotations

from typing import Any

from app.schemas import ForgetMemoryArgs, SaveMemoryArgs
from app.services import memory_service
from app.tools.registry import ToolContext, ToolError, register


@register("save_memory", "Remember a durable, non-sensitive fact for future conversations: response_style, preferred_language, preferred_name, contact_preference, programme, "
          "interest, plan, hostel_block, support_topic. NEVER use for OTPs, passwords, PINs, tokens, API keys, card/account numbers, health data or temporary statements.", SaveMemoryArgs)
def save_memory(ctx: ToolContext, a: SaveMemoryArgs) -> dict[str, Any]:
    try:
        m = memory_service.save(ctx.db, ctx.actor, a.key, a.value, trace_id=ctx.trace.trace_id)
    except memory_service.MemoryRejected as e:
        raise ToolError("MEMORY_REJECTED", f"Not stored: {e.reason}.")
    ctx.trace.memory_refs.append(m.id)
    return {"stored": True, "memory_id": m.id, "category": m.category, "key": m.memory_key, "value": m.memory_value}


@register("forget_memory", "Delete the student's stored memory: one item by memory_id, all items for a key, or everything when no argument is given.", ForgetMemoryArgs)
def forget_memory(ctx: ToolContext, a: ForgetMemoryArgs) -> dict[str, Any]:
    if a.memory_id is not None:
        removed = 1 if memory_service.delete_item(ctx.db, ctx.actor, a.memory_id) else 0
    elif a.key:
        removed = memory_service.delete_by_key(ctx.db, ctx.actor, a.key)
    else:
        removed = sum(1 for m in memory_service.list_all(ctx.db, ctx.actor.user_code) if memory_service.delete_item(ctx.db, ctx.actor, m.id))
    return {"removed": removed}
