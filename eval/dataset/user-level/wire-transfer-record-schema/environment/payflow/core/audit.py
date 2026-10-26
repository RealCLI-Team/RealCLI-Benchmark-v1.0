from __future__ import annotations

from payflow.domain.models import Approval, TransferCommand


def approvals_for(command: TransferCommand, risk: dict) -> list[Approval]:
    if risk["tier"] == "high":
        return [
            Approval("risk.bot", "risk-reviewer", command.requested_at),
            Approval("treasury.bot", "treasury-approver", command.requested_at),
        ]
    return [Approval("auto.bot", "policy-engine", command.requested_at)]


def trace_id_for(command: TransferCommand) -> str:
    return f"trace-{command.transfer_id}-{command.idempotency_key[-6:]}"
