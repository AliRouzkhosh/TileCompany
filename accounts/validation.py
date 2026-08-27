"""Country-aware normalization for customer contact identifiers."""

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


_DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_IRAN_COUNTRIES = {"ir", "iran"}


def normalize_digits(value):
    return (value or "").translate(_DIGIT_TRANSLATION)


def is_iran(country):
    return normalize_digits(country).strip().casefold() in _IRAN_COUNTRIES


def normalize_iranian_mobile(value):
    digits = re.sub(r"[\s-]", "", normalize_digits(value))
    if digits.startswith("+98"):
        digits = "0" + digits[3:]
    elif digits.startswith("98"):
        digits = "0" + digits[2:]
    if not re.fullmatch(r"09\d{9}", digits):
        raise ValidationError(_("Enter a valid Iranian mobile number."))
    return "+98" + digits[1:]


def normalize_iranian_postal_code(value):
    code = re.sub(r"[\s-]", "", normalize_digits(value))
    if not re.fullmatch(r"\d{10}", code):
        raise ValidationError(_("Enter a 10-digit postal code."))
    return code
