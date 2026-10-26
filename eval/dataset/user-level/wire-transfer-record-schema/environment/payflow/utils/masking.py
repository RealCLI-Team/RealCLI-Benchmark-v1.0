def mask_account(account_id: str) -> str:
    if len(account_id) <= 4:
        return "****"
    return f"****{account_id[-4:]}"
