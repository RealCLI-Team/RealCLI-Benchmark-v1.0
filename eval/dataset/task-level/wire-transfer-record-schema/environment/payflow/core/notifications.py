from __future__ import annotations


def notification_targets(risk: dict) -> list[str]:
    if risk["tier"] == "high":
        return ["risk-ops", "treasury"]
    if risk["tier"] == "medium":
        return ["risk-ops"]
    return []
