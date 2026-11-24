"""PII redaction for free text before logging or audit."""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?61[ -]?(?:\(0\)[ -]?)?|\(?0\)?[ -]?)"
    r"[2-478]\)?(?:[ -]?\d){8}(?!\d)"
)
CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

CARD_PLACEHOLDER = "[REDACTED:CREDIT_CARD]"
PHONE_PLACEHOLDER = "[REDACTED:PHONE]"
EMAIL_PLACEHOLDER = "[REDACTED:EMAIL]"


def luhn_check(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _redact_cards(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and luhn_check(digits):
            return CARD_PLACEHOLDER
        return match.group(0)

    return CARD_RE.sub(replace, text)


def redact_pii(text: str) -> str:
    """Replace common PII patterns with typed placeholders."""
    text = _redact_cards(text)
    text = PHONE_RE.sub(PHONE_PLACEHOLDER, text)
    text = EMAIL_RE.sub(EMAIL_PLACEHOLDER, text)
    return text
