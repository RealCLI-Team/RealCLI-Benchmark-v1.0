#!/bin/bash
set -Eeuo pipefail

cd /app
mkdir -p retail_ops

cat > retail_ops/config.py <<'PY'
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal


DEFAULT_SETTINGS = {
    "currency": "USD",
    "free_shipping_threshold": Decimal("120.00"),
    "handling_fee": Decimal("1.99"),
    "gift_wrap_fee": Decimal("4.95"),
    "warehouse": "CHI-01",
}

CATALOG = {
    "SKU-RED-MUG": {"price": Decimal("12.50"), "weight": Decimal("0.8"), "category": "home", "inventory": 50},
    "SKU-BLUE-PEN": {"price": Decimal("2.25"), "weight": Decimal("0.1"), "category": "stationery", "inventory": 200},
    "SKU-NOTEBOOK": {"price": Decimal("7.75"), "weight": Decimal("0.6"), "category": "stationery", "inventory": 80},
    "SKU-COFFEE": {"price": Decimal("18.00"), "weight": Decimal("1.0"), "category": "grocery", "inventory": 35},
    "SKU-LAMP": {"price": Decimal("45.00"), "weight": Decimal("3.5"), "category": "home", "inventory": 12},
}

TAX_RATES = {
    ("US", "CA"): Decimal("0.0825"),
    ("US", "NY"): Decimal("0.08875"),
    ("US", "*"): Decimal("0.06"),
    ("DE", "*"): Decimal("0.19"),
    ("GB", "*"): Decimal("0.20"),
    ("CA", "*"): Decimal("0.13"),
}

PROMOS = {
    "SAVE10": {"kind": "percent", "value": Decimal("0.10"), "minimum": Decimal("25.00"), "exclude_categories": {"grocery"}},
    "VIP20": {"kind": "percent", "value": Decimal("0.20"), "minimum": Decimal("80.00"), "tiers": {"gold", "platinum"}},
    "BULK5": {"kind": "amount", "value": Decimal("5.00"), "minimum_quantity": 10},
    "FREESHIP": {"kind": "free_shipping", "minimum": Decimal("50.00")},
}


def seed_catalog():
    return deepcopy(CATALOG)
PY

cat > retail_ops/store.py <<'PY'
from __future__ import annotations

from copy import deepcopy


class InMemoryStore:
    def __init__(self, catalog):
        self._catalog = deepcopy(catalog)

    def get_item(self, sku):
        item = self._catalog.get(sku)
        return deepcopy(item) if item else None

    def has_stock(self, sku, quantity):
        item = self._catalog.get(sku)
        return bool(item and item["inventory"] >= quantity)

    def reserve(self, lines):
        for line in lines:
            self._catalog[line["sku"]]["inventory"] -= line["quantity"]

    def inventory_snapshot(self):
        return {sku: row["inventory"] for sku, row in self._catalog.items()}

    def selected_inventory(self, lines):
        return {line["sku"]: self._catalog[line["sku"]]["inventory"] for line in lines}
PY

cat > retail_ops/notifications.py <<'PY'
from __future__ import annotations

from copy import deepcopy


class RecordingNotifier:
    def __init__(self):
        self.messages = []

    def receipt(self, customer, order_id):
        self.messages.append({"to": customer["email"], "kind": "receipt", "order_id": order_id})

    def picklist(self, order_id):
        self.messages.append({"to": "warehouse@example.com", "kind": "picklist", "order_id": order_id})

    def snapshot(self):
        return deepcopy(self.messages)
PY

cat > retail_ops/pricing.py <<'PY'
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from .config import PROMOS, TAX_RATES

CENT = Decimal("0.01")


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PriceBreakdown:
    subtotal: Decimal
    discount: Decimal
    gift_wrap: Decimal
    handling: Decimal
    tax: Decimal
    risk_flags: list[str]


class PricingService:
    def __init__(self, promos=None, tax_rates=None):
        self.promos = promos or PROMOS
        self.tax_rates = tax_rates or TAX_RATES

    def price_lines(self, raw_items, store):
        lines = []
        for raw in raw_items:
            sku = raw["sku"]
            quantity = raw["quantity"]
            item = store.get_item(sku)
            line_total = money(item["price"] * quantity)
            lines.append({
                "sku": sku,
                "quantity": quantity,
                "unit_price": money(item["price"]),
                "line_total": line_total,
                "weight": item["weight"] * quantity,
                "category": item["category"],
            })
        return lines

    def calculate(self, payload, customer, lines, settings):
        subtotal = money(sum((line["line_total"] for line in lines), Decimal("0")))
        promo_discount, flags = self._promo_discount(payload.get("promo_code"), customer, lines, subtotal)
        tier_discount = money(subtotal * self._loyalty_rate(customer.get("tier", "standard")))
        discount = min(money(promo_discount + tier_discount), subtotal)
        gift_wrap = money(settings["gift_wrap_fee"] if payload.get("gift_wrap") else Decimal("0"))
        handling = money(settings["handling_fee"])
        tax = self._tax(customer, subtotal - discount + gift_wrap + handling)
        return PriceBreakdown(subtotal, discount, gift_wrap, handling, tax, flags)

    def _promo_discount(self, code, customer, lines, subtotal):
        if not code:
            return Decimal("0"), []
        promo = self.promos.get(code)
        if not promo:
            return Decimal("0"), ["INVALID_PROMO"]
        handlers = {
            "percent": self._percent_discount,
            "amount": self._amount_discount,
            "free_shipping": self._free_shipping_marker,
        }
        return handlers[promo["kind"]](promo, customer, lines, subtotal)

    def _percent_discount(self, promo, customer, lines, subtotal):
        if subtotal < promo.get("minimum", Decimal("0")):
            return Decimal("0"), ["PROMO_MINIMUM_NOT_MET"]
        tiers = promo.get("tiers")
        if tiers and customer.get("tier", "standard") not in tiers:
            return Decimal("0"), ["PROMO_TIER_MISMATCH"]
        excluded = promo.get("exclude_categories", set())
        eligible = sum((line["line_total"] for line in lines if line["category"] not in excluded), Decimal("0"))
        return money(eligible * promo["value"]), []

    def _amount_discount(self, promo, customer, lines, subtotal):
        quantity = sum(line["quantity"] for line in lines)
        if quantity < promo.get("minimum_quantity", 0):
            return Decimal("0"), ["PROMO_MINIMUM_NOT_MET"]
        return money(promo["value"]), []

    def _free_shipping_marker(self, promo, customer, lines, subtotal):
        if subtotal < promo.get("minimum", Decimal("0")):
            return Decimal("0"), ["PROMO_MINIMUM_NOT_MET"]
        return Decimal("0"), []

    def _loyalty_rate(self, tier):
        return {"gold": Decimal("0.03"), "platinum": Decimal("0.05")}.get(tier, Decimal("0"))

    def _tax(self, customer, taxable):
        country = customer.get("country")
        state = customer.get("state")
        rate = self.tax_rates.get((country, state), self.tax_rates.get((country, "*"), Decimal("0")))
        return money(max(taxable, Decimal("0")) * rate)
PY

cat > retail_ops/shipping.py <<'PY'
from __future__ import annotations

from decimal import Decimal

from .config import PROMOS
from .pricing import money


class ShippingService:
    def __init__(self, promos=None):
        self.promos = promos or PROMOS
        self.base_rates = {
            "standard": (Decimal("5.00"), Decimal("0.75")),
            "express": (Decimal("12.00"), Decimal("1.25")),
            "overnight": (Decimal("25.00"), Decimal("2.50")),
        }

    def quote(self, payload, customer, lines, subtotal, discount, settings, risk_flags):
        method = payload.get("shipping_method") or "standard"
        total_weight = self.total_weight(lines)
        if self._is_free(payload.get("promo_code"), subtotal, discount, settings):
            return Decimal("0.00"), method, risk_flags
        base, per_weight = self.base_rates.get(method, self.base_rates["standard"])
        if method not in self.base_rates:
            risk_flags = [*risk_flags, "UNKNOWN_SHIPPING_METHOD"]
        shipping = base + total_weight * per_weight
        if customer.get("country") != "US":
            shipping += Decimal("8.50")
        if total_weight > Decimal("10"):
            shipping += Decimal("6.00")
        return money(shipping), method, risk_flags

    def packages(self, lines, method, settings):
        total_weight = self.total_weight(lines)
        if total_weight <= Decimal("5"):
            return [self._package(settings, method, total_weight, [line["sku"] for line in lines])]
        return [self._package(settings, method, line["weight"], [line["sku"]]) for line in lines]

    def total_weight(self, lines):
        return sum((line["weight"] for line in lines), Decimal("0"))

    def _is_free(self, code, subtotal, discount, settings):
        promo = self.promos.get(code or "")
        promo_free = promo and promo["kind"] == "free_shipping" and subtotal >= promo["minimum"]
        threshold_free = subtotal - discount >= settings["free_shipping_threshold"]
        return bool(promo_free or threshold_free)

    def _package(self, settings, method, weight, skus):
        return {
            "warehouse": settings["warehouse"],
            "method": method,
            "weight": float(money(weight)),
            "skus": skus,
        }
PY

cat > retail_ops/processor.py <<'PY'
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .config import DEFAULT_SETTINGS, seed_catalog
from .notifications import RecordingNotifier
from .pricing import PricingService, money
from .shipping import ShippingService
from .store import InMemoryStore


def process_order(payload, now=None, store=None, notifier=None, settings=None):
    stamp = now or datetime.now(timezone.utc).isoformat()
    settings = dict(DEFAULT_SETTINGS if settings is None else settings)
    store = store or InMemoryStore(seed_catalog())
    notifier = notifier or RecordingNotifier()
    rejection = validate_order(payload, store, stamp, settings)
    if rejection:
        return rejection

    customer = payload["customer"]
    pricing = PricingService()
    shipping_service = ShippingService()
    lines = pricing.price_lines(payload["items"], store)
    prices = pricing.calculate(payload, customer, lines, settings)
    shipping, method, risk_flags = shipping_service.quote(
        payload, customer, lines, prices.subtotal, prices.discount, settings, prices.risk_flags
    )
    total = money(prices.subtotal - prices.discount + shipping + prices.gift_wrap + prices.handling + prices.tax)

    packages = shipping_service.packages(lines, method, settings)
    store.reserve(lines)
    ledger_entries = accepted_ledger(payload["order_id"], stamp, total, lines)
    notifier.receipt(customer, payload["order_id"])
    notifier.picklist(payload["order_id"])
    return accepted_response(
        payload, customer, prices, shipping, total, packages, ledger_entries,
        notifier.snapshot()[-2:], store.selected_inventory(lines), risk_flags, settings
    )


def validate_order(payload, store, stamp, settings):
    if not isinstance(payload, dict):
        return rejected_response("UNKNOWN", "INVALID_PAYLOAD", stamp, settings)
    customer = payload.get("customer") or {}
    order_id = payload.get("order_id") or "UNKNOWN"
    if not customer.get("id") or not customer.get("email"):
        return rejected_response(order_id, "INVALID_CUSTOMER", stamp, settings)
    items = payload.get("items") or []
    if not items:
        return rejected_response(order_id, "EMPTY_ORDER", stamp, settings)
    for raw in items:
        reason = invalid_item_reason(raw, store)
        if reason:
            return rejected_response(order_id, reason, stamp, settings)
    return None


def invalid_item_reason(raw, store):
    sku = raw.get("sku")
    quantity = raw.get("quantity")
    if store.get_item(sku) is None:
        return "UNKNOWN_SKU"
    if not isinstance(quantity, int) or quantity <= 0:
        return "INVALID_QUANTITY"
    if not store.has_stock(sku, quantity):
        return "INSUFFICIENT_STOCK"
    return None


def accepted_ledger(order_id, stamp, total, lines):
    return [
        {"event": "order_accepted", "order_id": order_id, "occurred_at": stamp, "amount": _as_float(total)},
        {"event": "inventory_reserved", "order_id": order_id, "skus": [line["sku"] for line in lines]},
    ]


def accepted_response(payload, customer, prices, shipping, total, packages, ledger, notifications, remaining, risk_flags, settings):
    return {
        "order_id": payload["order_id"],
        "status": "accepted",
        "customer_id": customer["id"],
        "subtotal": _as_float(prices.subtotal),
        "discount": _as_float(prices.discount),
        "tax": _as_float(prices.tax),
        "shipping": _as_float(shipping),
        "gift_wrap": _as_float(prices.gift_wrap),
        "handling": _as_float(prices.handling),
        "total": _as_float(total),
        "currency": settings["currency"],
        "packages": packages,
        "ledger_entries": ledger,
        "notifications": notifications,
        "remaining_inventory": remaining,
        "risk_flags": risk_flags,
    }


def rejected_response(order_id, reason, stamp, settings):
    entry = {"event": "order_rejected", "order_id": order_id, "occurred_at": stamp, "reason": reason}
    return {
        "order_id": order_id,
        "status": "rejected",
        "reason": reason,
        "subtotal": 0.0,
        "discount": 0.0,
        "tax": 0.0,
        "shipping": 0.0,
        "gift_wrap": 0.0,
        "handling": 0.0,
        "total": 0.0,
        "currency": settings["currency"],
        "packages": [],
        "ledger_entries": [entry],
        "notifications": [],
        "remaining_inventory": {},
        "risk_flags": [],
    }


def _as_float(value):
    if isinstance(value, Decimal):
        return float(value)
    return value
PY

cat > retail_ops/legacy_pipeline.py <<'PY'
from __future__ import annotations

from .processor import process_order

__all__ = ["process_order"]
PY

cat > retail_ops/__init__.py <<'PY'
from .processor import process_order

__all__ = ["process_order"]
PY

cat > retail_ops/reporting.py <<'PY'
import json
import sys

from . import process_order


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        with open(argv[0], "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        payload = json.load(sys.stdin)
    print(json.dumps(process_order(payload), sort_keys=True))


if __name__ == "__main__":
    main()
PY

python -m compileall -q retail_ops
