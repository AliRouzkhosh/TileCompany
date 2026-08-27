"""Locale-aware, presentation-only date and time filters."""

import jdatetime

from django import template
from django.utils import formats, timezone
from django.utils.translation import get_language

from .pricing import format_localized_number


register = template.Library()


def _is_persian():
    return (get_language() or "").lower().replace("_", "-").startswith("fa")


def _local(value):
    return timezone.localtime(value) if timezone.is_aware(value) else value


def _jalali(value):
    return jdatetime.datetime.fromgregorian(datetime=_local(value))


def _date_part(value, width=0):
    return format_localized_number(value, decimals=0).replace("٬", "").rjust(width, "۰")


@register.filter
def localized_date(value):
    if not value:
        return ""
    if _is_persian():
        date = _jalali(value)
        return "/".join((_date_part(date.year), _date_part(date.month, 2), _date_part(date.day, 2)))
    return formats.date_format(_local(value), "DATE_FORMAT")


@register.filter
def localized_time(value):
    if not value:
        return ""
    if _is_persian():
        date = _jalali(value)
        return f"{_date_part(date.hour, 2)}:{_date_part(date.minute, 2)}"
    return formats.time_format(_local(value), "TIME_FORMAT")


@register.filter
def localized_datetime(value):
    if not value:
        return ""
    if _is_persian():
        return f"{localized_date(value)}، {localized_time(value)}"
    return f"{localized_date(value)} {localized_time(value)}"
