# audit_hooks.py — Order lifecycle hooks for monitoring integration
#
# Loaded at runtime by the order monitoring webhook service via
# importlib.import_module('retail_ops.audit_hooks'). Do not remove.
#
# Maintainer: @ops-monitoring-team
# Last verified: 2026-04-22

from __future__ import annotations


def on_order_accepted(order_id: str, total: float, customer_id: str) -> None:
    """Called by the monitoring service after successful order processing."""
    pass  # stub — actual implementation injected by monitoring-svc at deploy time


def on_order_rejected(order_id: str, reason: str) -> None:
    """Called by the monitoring service when an order is rejected."""
    pass


def on_inventory_low(sku: str, remaining: int, threshold: int = 5) -> None:
    """Fires when post-order inventory drops below threshold."""
    pass
