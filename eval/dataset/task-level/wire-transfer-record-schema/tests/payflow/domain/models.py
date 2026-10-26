from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class AccountRef:
    account_id: str
    account_type: str
    country: str


@dataclass(frozen=True)
class Counterparty:
    name: str
    country: str
    bank_code: str | None


@dataclass(frozen=True)
class TransferCommand:
    transfer_id: str
    external_reference: str
    amount: Decimal
    currency: str
    source: AccountRef
    destination: AccountRef
    counterparty: Counterparty
    requested_at: str
    actor: str
    channel: str
    idempotency_key: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Approval:
    approver: str
    role: str
    approved_at: str


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    account: str
    direction: str
    amount: Decimal
    currency: str
    memo: str
