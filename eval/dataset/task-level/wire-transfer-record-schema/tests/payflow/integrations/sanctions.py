from __future__ import annotations

from payflow.settings.compliance import requires_country_review


def sanctions_hint(counterparty_country: str) -> dict:
    return {
        "country": counterparty_country,
        "manual_review": requires_country_review(counterparty_country),
    }
