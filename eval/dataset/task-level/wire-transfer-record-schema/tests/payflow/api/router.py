from __future__ import annotations

from payflow.api.auth import actor_roles, require_actor
from payflow.api.events import handle_event


def dispatch(route: str, payload: dict) -> dict:
    if route != "POST /wire-transfers":
        raise LookupError(route)
    actor = require_actor(payload)
    if "wire-transfer" not in actor_roles(actor):
        raise PermissionError("actor cannot submit wire transfer")
    return handle_event(payload)
