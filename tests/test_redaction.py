from payment_triage.redaction import luhn_check, redact_pii


def test_email_redacted() -> None:
    assert redact_pii("contact alice@example.com") == "contact [REDACTED:EMAIL]"


def test_phone_redacted() -> None:
    assert redact_pii("call +61 412 345 678") == "call [REDACTED:PHONE]"


def test_valid_card_redacted() -> None:
    assert redact_pii("card 4111 1111 1111 1111") == "card [REDACTED:CREDIT_CARD]"


def test_invalid_card_not_redacted() -> None:
    text = "number 4111 1111 1111 1112"
    assert redact_pii(text) == text


def test_plain_text_unchanged() -> None:
    assert redact_pii("no sensitive data here") == "no sensitive data here"


def test_luhn() -> None:
    assert luhn_check("4111111111111111") is True
    assert luhn_check("4111111111111112") is False
