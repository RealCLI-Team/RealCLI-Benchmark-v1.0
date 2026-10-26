from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


SETTINGS = {
    "currency": "USD",
    "free_shipping_threshold": 120.00,
    "handling_fee": 1.99,
    "gift_wrap_fee": 4.95,
    "warehouse": "CHI-01",
}

DB = {
    "SKU-RED-MUG": {"price": 12.50, "weight": 0.8, "category": "home", "inventory": 50},
    "SKU-BLUE-PEN": {"price": 2.25, "weight": 0.1, "category": "stationery", "inventory": 200},
    "SKU-NOTEBOOK": {"price": 7.75, "weight": 0.6, "category": "stationery", "inventory": 80},
    "SKU-COFFEE": {"price": 18.00, "weight": 1.0, "category": "grocery", "inventory": 35},
    "SKU-LAMP": {"price": 45.00, "weight": 3.5, "category": "home", "inventory": 12},
}

TAX_RATES = {
    ("US", "CA"): 0.0825,
    ("US", "NY"): 0.08875,
    ("US", "*"): 0.06,
    ("DE", "*"): 0.19,
    ("GB", "*"): 0.20,
    ("CA", "*"): 0.13,
}

PROMO_CODES = {
    "SAVE10": {"kind": "percent", "value": 0.10, "minimum": 25.00, "exclude_categories": {"grocery"}},
    "VIP20": {"kind": "percent", "value": 0.20, "minimum": 80.00, "tiers": {"gold", "platinum"}},
    "BULK5": {"kind": "amount", "value": 5.00, "minimum_quantity": 10},
    "FREESHIP": {"kind": "free_shipping", "minimum": 50.00},
}

SENT_EMAILS = []
LEDGER = []


class OrderProcessor:
    def __init__(self):
        self.settings = SETTINGS
        self.db = DB
        self.tax_rates = TAX_RATES
        self.promos = PROMO_CODES
        self.emails = SENT_EMAILS
        self.ledger = LEDGER

    def process(self, payload, now=None):
        stamp = now or datetime.now(timezone.utc).isoformat()
        risk_flags = []
        if not isinstance(payload, dict):
            return self._reject("UNKNOWN", "INVALID_PAYLOAD", stamp, risk_flags)
        order_id = payload.get("order_id") or "UNKNOWN"
        customer = payload.get("customer") or {}
        if not customer.get("id") or not customer.get("email"):
            return self._reject(order_id, "INVALID_CUSTOMER", stamp, risk_flags)
        items = payload.get("items") or []
        if not items:
            return self._reject(order_id, "EMPTY_ORDER", stamp, risk_flags)

        prepared = []
        subtotal = 0.0
        total_weight = 0.0
        quantity_total = 0
        for raw in items:
            sku = raw.get("sku")
            quantity = raw.get("quantity")
            if sku not in self.db:
                return self._reject(order_id, "UNKNOWN_SKU", stamp, risk_flags)
            if not isinstance(quantity, int) or quantity <= 0:
                return self._reject(order_id, "INVALID_QUANTITY", stamp, risk_flags)
            if self.db[sku]["inventory"] < quantity:
                return self._reject(order_id, "INSUFFICIENT_STOCK", stamp, risk_flags)
            row = self.db[sku]
            line_total = round(row["price"] * quantity, 2)
            subtotal = round(subtotal + line_total, 2)
            total_weight = round(total_weight + row["weight"] * quantity, 4)
            quantity_total += quantity
            prepared.append(
                {
                    "sku": sku,
                    "quantity": quantity,
                    "unit_price": round(row["price"], 2),
                    "line_total": line_total,
                    "weight": row["weight"] * quantity,
                    "category": row["category"],
                }
            )

        promo_code = payload.get("promo_code")
        discount = 0.0
        if promo_code:
            promo = self.promos.get(promo_code)
            if promo is None:
                risk_flags.append("INVALID_PROMO")
            elif promo["kind"] == "percent":
                if subtotal >= promo.get("minimum", 0.0):
                    tiers = promo.get("tiers")
                    if tiers is None or customer.get("tier", "standard") in tiers:
                        excluded = promo.get("exclude_categories", set())
                        eligible = 0.0
                        for line in prepared:
                            if line["category"] not in excluded:
                                eligible = round(eligible + line["line_total"], 2)
                        discount = round(discount + eligible * promo["value"], 2)
                    else:
                        risk_flags.append("PROMO_TIER_MISMATCH")
                else:
                    risk_flags.append("PROMO_MINIMUM_NOT_MET")
            elif promo["kind"] == "amount":
                if quantity_total >= promo.get("minimum_quantity", 0):
                    discount = round(discount + promo["value"], 2)
                else:
                    risk_flags.append("PROMO_MINIMUM_NOT_MET")
            elif promo["kind"] == "free_shipping":
                if subtotal < promo.get("minimum", 0.0):
                    risk_flags.append("PROMO_MINIMUM_NOT_MET")

        tier = customer.get("tier", "standard")
        if tier == "gold":
            discount = round(discount + subtotal * 0.03, 2)
        elif tier == "platinum":
            discount = round(discount + subtotal * 0.05, 2)
        discount = min(discount, subtotal)

        method = payload.get("shipping_method") or "standard"
        if promo_code == "FREESHIP" and subtotal >= self.promos["FREESHIP"]["minimum"]:
            shipping = 0.0
        elif subtotal - discount >= self.settings["free_shipping_threshold"]:
            shipping = 0.0
        elif method == "standard":
            shipping = 5.00 + total_weight * 0.75
        elif method == "express":
            shipping = 12.00 + total_weight * 1.25
        elif method == "overnight":
            shipping = 25.00 + total_weight * 2.50
        else:
            risk_flags.append("UNKNOWN_SHIPPING_METHOD")
            shipping = 5.00 + total_weight * 0.75
        if customer.get("country") != "US":
            shipping += 8.50
        if total_weight > 10:
            shipping += 6.00
        shipping = round(shipping, 2)

        gift_wrap = self.settings["gift_wrap_fee"] if payload.get("gift_wrap") else 0.0
        handling = self.settings["handling_fee"]
        tax_rate = self.tax_rates.get(
            (customer.get("country"), customer.get("state")),
            self.tax_rates.get((customer.get("country"), "*"), 0.0),
        )
        taxable = max(0.0, subtotal - discount + gift_wrap + handling)
        tax = round(taxable * tax_rate, 2)
        total = round(subtotal - discount + shipping + gift_wrap + handling + tax, 2)

        packages = []
        if total_weight <= 5:
            packages.append(
                {
                    "warehouse": self.settings["warehouse"],
                    "method": method,
                    "weight": round(total_weight, 2),
                    "skus": [line["sku"] for line in prepared],
                }
            )
        else:
            for line in prepared:
                packages.append(
                    {
                        "warehouse": self.settings["warehouse"],
                        "method": method,
                        "weight": round(line["weight"], 2),
                        "skus": [line["sku"]],
                    }
                )

        for line in prepared:
            self.db[line["sku"]]["inventory"] -= line["quantity"]
        remaining = {line["sku"]: self.db[line["sku"]]["inventory"] for line in prepared}
        ledger_entries = [
            {"event": "order_accepted", "order_id": order_id, "occurred_at": stamp, "amount": total},
            {"event": "inventory_reserved", "order_id": order_id, "skus": [line["sku"] for line in prepared]},
        ]
        self.ledger.extend(deepcopy(ledger_entries))
        self.emails.append({"to": customer["email"], "kind": "receipt", "order_id": order_id})
        self.emails.append({"to": "warehouse@example.com", "kind": "picklist", "order_id": order_id})
        return {
            "order_id": order_id,
            "status": "accepted",
            "customer_id": customer["id"],
            "subtotal": subtotal,
            "discount": discount,
            "tax": tax,
            "shipping": shipping,
            "gift_wrap": round(gift_wrap, 2),
            "handling": round(handling, 2),
            "total": total,
            "currency": self.settings["currency"],
            "packages": packages,
            "ledger_entries": ledger_entries,
            "notifications": deepcopy(self.emails[-2:]),
            "remaining_inventory": remaining,
            "risk_flags": risk_flags,
        }

    def _reject(self, order_id, reason, stamp, risk_flags):
        entry = {"event": "order_rejected", "order_id": order_id, "occurred_at": stamp, "reason": reason}
        self.ledger.append(entry)
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
            "currency": self.settings["currency"],
            "packages": [],
            "ledger_entries": [entry],
            "notifications": [],
            "remaining_inventory": {},
            "risk_flags": risk_flags,
        }


def process_order(payload, now=None):
    return OrderProcessor().process(payload, now=now)
