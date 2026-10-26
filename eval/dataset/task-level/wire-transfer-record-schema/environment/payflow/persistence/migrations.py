MIGRATIONS = [
    {"version": 1, "description": "initial ledger mirror"},
    {"version": 2, "description": "add risk block"},
    {"version": 3, "description": "add audit approvals and metadata tags"},
]


def latest_version() -> int:
    return MIGRATIONS[-1]["version"]
