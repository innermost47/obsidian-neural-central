import re
from typing import Tuple
from disposable_email_domains import blocklist


class EmailValidator:
    DISPOSABLE_DOMAINS = {
        "tempmail.com",
        "guerrillamail.com",
        "10minutemail.com",
        "mailinator.com",
        "yopmail.com",
        "throwaway.email",
        "temp-mail.org",
        "getnada.com",
        "maildrop.cc",
        "trashmail.com",
        "emailondeck.com",
        "fakeinbox.com",
        "dispostable.com",
        "mohmal.com",
        "mintemail.com",
        "sharklasers.com",
        "guerrillamail.info",
        "grr.la",
        "spam4.me",
        "mailnesia.com",
        "emailsensei.com",
    }

    @staticmethod
    def is_valid_email_format(email: str) -> Tuple[bool, str]:
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, email):
            return False, "Invalid email format"
        return True, ""

    @staticmethod
    def is_disposable_email(email: str) -> Tuple[bool, str]:
        domain = email.split("@")[1].lower()

        if domain in blocklist:
            return True, f"Disposable email domain detected: {domain}"

        if domain in EmailValidator.DISPOSABLE_DOMAINS:
            return True, f"Disposable email domain detected: {domain}"

        suspicious_patterns = [
            "temp",
            "trash",
            "disposable",
            "throwaway",
            "fake",
            "spam",
            "guerrilla",
            "minute",
        ]
        if any(pattern in domain for pattern in suspicious_patterns):
            return True, f"Suspicious email domain: {domain}"

        return False, ""

    @staticmethod
    def validate_email(email: str) -> Tuple[bool, str]:
        email = email.strip().lower()

        is_valid, error = EmailValidator.is_valid_email_format(email)
        if not is_valid:
            return False, error

        is_disposable, error = EmailValidator.is_disposable_email(email)
        if is_disposable:
            return False, error

        return True, ""
