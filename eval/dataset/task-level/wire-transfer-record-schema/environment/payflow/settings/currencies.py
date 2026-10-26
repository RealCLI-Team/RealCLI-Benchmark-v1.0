CURRENCY_EXPONENTS = {"USD": 2, "EUR": 2, "GBP": 2}


def exponent_for(currency: str) -> int:
    return CURRENCY_EXPONENTS.get(currency, 2)
