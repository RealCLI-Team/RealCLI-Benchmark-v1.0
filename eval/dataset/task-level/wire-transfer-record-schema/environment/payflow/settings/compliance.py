HIGH_RISK_COUNTRIES = {"IR", "KP", "SY"}
MANUAL_REVIEW_THRESHOLD = "10000.00"


def requires_country_review(country: str) -> bool:
    return country in HIGH_RISK_COUNTRIES
