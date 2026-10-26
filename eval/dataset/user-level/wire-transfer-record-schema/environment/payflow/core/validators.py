from __future__ import annotations

from decimal import Decimal

from payflow.domain.enums import ALLOWED_ACCOUNT_TYPES, ALLOWED_CHANNELS, SUPPORTED_CURRENCIES
from payflow.domain.errors import ValidationError
from payflow.domain.models import TransferCommand


def validate_command(command: TransferCommand) -> None:
    if command.amount <= Decimal("0"):
        raise ValidationError("amount must be positive")
    if command.currency not in SUPPORTED_CURRENCIES:
        raise ValidationError(f"unsupported currency {command.currency}")
    if command.channel not in ALLOWED_CHANNELS:
        raise ValidationError(f"unsupported channel {command.channel}")
    if command.source.account_type not in ALLOWED_ACCOUNT_TYPES:
        raise ValidationError("unsupported source account type")
    if command.destination.account_type not in ALLOWED_ACCOUNT_TYPES:
        raise ValidationError("unsupported destination account type")
