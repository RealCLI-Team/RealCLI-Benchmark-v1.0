from __future__ import annotations


def require_actor(payload: dict) -> str:
    actor = (payload.get("audit") or {}).get("actor")
    if not actor:
        raise PermissionError("wire transfer request requires audit.actor")
    return actor


def actor_roles(actor: str) -> set[str]:
    if actor.endswith("@example.com"):
        return {"operator", "wire-transfer"}
    return {"readonly"}
