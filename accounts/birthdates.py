"""Locale-aware birthdate form/presentation helpers with Gregorian persistence."""

from datetime import date

import jdatetime

from django import forms
from django.utils import formats
from django.utils.translation import get_language, gettext_lazy as _

from .validation import normalize_digits


JALALI_MONTH_CHOICES = (
    ("1", _("Farvardin")),
    ("2", _("Ordibehesht")),
    ("3", _("Khordad")),
    ("4", _("Tir")),
    ("5", _("Mordad")),
    ("6", _("Shahrivar")),
    ("7", _("Mehr")),
    ("8", _("Aban")),
    ("9", _("Azar")),
    ("10", _("Dey")),
    ("11", _("Bahman")),
    ("12", _("Esfand")),
)


def is_persian_language(language=None):
    return (language or get_language() or "").lower().replace("_", "-").startswith("fa")


class NormalizedIntegerField(forms.IntegerField):
    def to_python(self, value):
        return super().to_python(normalize_digits(str(value)) if value not in self.empty_values else value)


class JalaliBirthdateWidget(forms.MultiWidget):
    template_name = "accounts/widgets/jalali_birthdate.html"

    def __init__(self, *, css_class, attrs=None):
        numeric_attrs = {
            "class": "jalali-birthdate__control",
            "inputmode": "numeric",
            "dir": "ltr",
        }
        widgets = (
            forms.TextInput(attrs={**numeric_attrs, "autocomplete": "bday-year", "maxlength": "4"}),
            forms.Select(
                choices=(("", _("Select month")),) + JALALI_MONTH_CHOICES,
                attrs={"class": "jalali-birthdate__control", "autocomplete": "bday-month"},
            ),
            forms.TextInput(attrs={**numeric_attrs, "autocomplete": "bday-day", "maxlength": "2"}),
        )
        super().__init__(widgets, attrs)

    def decompress(self, value):
        if not value:
            return (None, None, None)
        if isinstance(value, str):
            try:
                value = date.fromisoformat(value)
            except ValueError:
                return (None, None, None)
        jalali = jdatetime.date.fromgregorian(date=value)
        return (jalali.year, str(jalali.month), jalali.day)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["preview"] = self._preview_for_stored_value(value)
        return context

    @staticmethod
    def _preview_for_stored_value(value):
        """Show a display-only Jalali value only for an existing valid date."""
        if not isinstance(value, date):
            return ""
        jalali = jdatetime.date.fromgregorian(date=value)
        month_label = dict(JALALI_MONTH_CHOICES)[str(jalali.month)]
        preview = f"{jalali.day} {month_label} {jalali.year}"
        return preview.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


class JalaliBirthdateField(forms.MultiValueField):
    default_error_messages = {
        "incomplete": _("Enter a complete birth date."),
        "invalid": _("Enter a valid birth date."),
    }

    def __init__(self, *, css_class, label, help_text="", required=False):
        fields = (
            NormalizedIntegerField(min_value=1, max_value=9999, required=False),
            forms.ChoiceField(choices=(("", _("Select month")),) + JALALI_MONTH_CHOICES, required=False),
            NormalizedIntegerField(min_value=1, max_value=31, required=False),
        )
        super().__init__(
            fields=fields,
            widget=JalaliBirthdateWidget(css_class=css_class),
            require_all_fields=False,
            required=required,
            label=label,
            help_text=help_text,
        )

    def compress(self, data_list):
        if not data_list or not any(value not in self.empty_values for value in data_list):
            return None
        if len(data_list) != 3 or any(value in self.empty_values for value in data_list):
            raise forms.ValidationError(self.error_messages["incomplete"], code="incomplete")
        try:
            return jdatetime.date(int(data_list[0]), int(data_list[1]), int(data_list[2])).togregorian()
        except (TypeError, ValueError, OverflowError):
            raise forms.ValidationError(self.error_messages["invalid"], code="invalid") from None


def build_birthdate_field(*, css_class, label, help_text="", required=False, language=None):
    if is_persian_language(language):
        return JalaliBirthdateField(
            css_class=css_class,
            label=label,
            help_text=help_text,
            required=required,
        )
    return forms.DateField(
        required=required,
        label=label,
        help_text=help_text,
        widget=forms.DateInput(attrs={"type": "date", "class": css_class}),
    )


def format_birthdate(value, *, language=None):
    """Format one stored Gregorian DateField value for human presentation."""
    if not value:
        return ""
    if not is_persian_language(language):
        return formats.date_format(value, "DATE_FORMAT")
    jalali = jdatetime.date.fromgregorian(date=value)
    ascii_value = f"{jalali.year:04d}/{jalali.month:02d}/{jalali.day:02d}"
    return ascii_value.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
