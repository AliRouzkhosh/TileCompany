"""Strict identity-phone parsing; deliberately separate from contact-phone fields."""
import phonenumbers
from django.conf import settings
from django.core.exceptions import ValidationError

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_phone_identity(raw_phone: str) -> str:
    value = (raw_phone or "").translate(_DIGITS).strip().replace(" ", "").replace("-", "")
    if not value:
        raise ValidationError("A phone number is required.")
    try:
        number = phonenumbers.parse(value, None if value.startswith("+") else settings.SMS_DEFAULT_REGION)
    except phonenumbers.NumberParseException as exc:
        raise ValidationError("Enter a valid phone number.") from exc
    if not phonenumbers.is_valid_number(number):
        raise ValidationError("Enter a valid phone number.")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
