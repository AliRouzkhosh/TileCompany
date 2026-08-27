################# accounts/forms.py ###############
from django import forms
from django.core.exceptions import ValidationError
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.db import transaction
from django.utils.translation import gettext_lazy as _, pgettext_lazy

from .models import (
    Address,
    Profile,
)
from .birthdates import build_birthdate_field
from .validation import is_iran, normalize_digits, normalize_iranian_mobile, normalize_iranian_postal_code


class SmsPhoneForm(forms.Form):
    phone = forms.CharField(label=_("Mobile number"), widget=forms.TextInput(attrs={"class": "sf-auth-control", "autocomplete": "tel", "inputmode": "tel", "dir": "ltr"}))


class SmsCodeForm(forms.Form):
    code = forms.CharField(label=_("Verification code"), max_length=6, min_length=6, widget=forms.TextInput(attrs={"class": "sf-auth-control tracking-[0.35em] text-center", "autocomplete": "one-time-code", "inputmode": "numeric", "pattern": "[0-9]*", "dir": "ltr"}))

    def clean_code(self):
        code = normalize_digits(self.cleaned_data["code"]).strip()
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise forms.ValidationError(_("Enter the six-digit code."))
        return code


# =====================================================================
# Shared Widget Styles
# =====================================================================

INPUT_CLASSES = (
    "block w-full rounded-xl border border-stone-300 bg-white "
    "px-4 py-3 text-sm text-stone-900 placeholder:text-stone-400 "
    "shadow-sm transition "
    "focus:border-amber-500 focus:ring-4 focus:ring-amber-100 "
    "focus:outline-none"
)

SELECT_CLASSES = (
    "block w-full rounded-xl border border-stone-300 bg-white "
    "px-4 py-3 text-sm text-stone-900 shadow-sm transition "
    "focus:border-amber-500 focus:ring-4 focus:ring-amber-100 "
    "focus:outline-none"
)

TEXTAREA_CLASSES = (
    "block w-full rounded-xl border border-stone-300 bg-white "
    "px-4 py-3 text-sm text-stone-900 placeholder:text-stone-400 "
    "shadow-sm transition resize-y "
    "focus:border-amber-500 focus:ring-4 focus:ring-amber-100 "
    "focus:outline-none"
)

CHECKBOX_CLASSES = (
    "h-4 w-4 rounded border-stone-300 text-amber-500 "
    "focus:ring-amber-400"
)

AUTH_INPUT_CLASSES = "sf-auth-control"
AUTH_SELECT_CLASSES = "sf-auth-control"
AUTH_CHECKBOX_CLASSES = "h-4 w-4 rounded border-border-strong text-brand focus:ring-brand"


# =====================================================================
# Registration
# =====================================================================

class BaseRegistrationForm(UserCreationForm):
    """Shared account and Profile contract for standard and SMS registration."""

    email_required = True

    username = forms.CharField(
        label=_("Username"),
        widget=forms.TextInput(
            attrs={
                "class": AUTH_INPUT_CLASSES,
                "placeholder": _("Choose a username"),
                "autocomplete": "username",
            }
        )
    )

    email = forms.EmailField(
        required=True,
        label=_("Email address"),
        widget=forms.EmailInput(
            attrs={
                "class": AUTH_INPUT_CLASSES,
                "placeholder": _("Email address"),
                "autocomplete": "email",
            }
        ),
    )

    password1 = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(
            attrs={
                "class": AUTH_INPUT_CLASSES,
                "placeholder": _("Create a password"),
                "autocomplete": "new-password",
            }
        )
    )

    password2 = forms.CharField(
        label=_("Password confirmation"),
        widget=forms.PasswordInput(
            attrs={
                "class": AUTH_INPUT_CLASSES,
                "placeholder": _("Confirm your password"),
                "autocomplete": "new-password",
            }
        )
    )

    birthdate = forms.DateField(required=False)

    gender = forms.ChoiceField(
        required=False,
        label=_("Gender"),
        choices=[("", _("Prefer not to say"))] + Profile.GENDER_CHOICES,
        widget=forms.Select(
            attrs={
                "class": AUTH_SELECT_CLASSES,
            }
        ),
    )

    is_business = forms.BooleanField(
        required=False,
        label=_("I'm purchasing as a business/company"),
        widget=forms.CheckboxInput(
            attrs={
                "class": AUTH_CHECKBOX_CLASSES,
            }
        ),
    )

    company_name = forms.CharField(
        required=False,
        max_length=200,
        label=_("Company name"),
        widget=forms.TextInput(
            attrs={
                "class": AUTH_INPUT_CLASSES,
                "placeholder": _("Company name"),
            }
        ),
    )

    company_tax_id = forms.CharField(
        required=False,
        max_length=100,
        label=_("Tax / VAT ID (optional)"),
        widget=forms.TextInput(
            attrs={
                "class": AUTH_INPUT_CLASSES,
                "placeholder": _("Tax / VAT ID"),
            }
        ),
    )

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "password1",
            "password2",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = self.email_required
        self.fields["birthdate"] = build_birthdate_field(
            css_class=AUTH_INPUT_CLASSES,
            label=_("Birth date"),
            help_text=_("Optional"),
        )

    def clean_email(self):
        email = self.cleaned_data["email"].strip()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_("This email is already used for an account. Please sign in or reset your password."))
        return email

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]

        if commit:
            user.save()

            Profile.objects.update_or_create(
                user=user,
                defaults={
                    "birthdate": self.cleaned_data.get("birthdate"),
                    "gender": self.cleaned_data.get("gender", ""),
                    "is_business": self.cleaned_data.get("is_business", False),
                    "company_name": self.cleaned_data.get("company_name", ""),
                    "company_tax_id": self.cleaned_data.get("company_tax_id", ""),
                },
            )

        return user


class RegisterForm(BaseRegistrationForm):
    """Standard signup retains its required-email policy."""


class SmsSignupCompletionForm(BaseRegistrationForm):
    """SMS signup shares the Profile contract while allowing a blank email."""

    email_required = False


# =====================================================================
# Profile
# =====================================================================
class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
        ]

        labels = {
            "first_name": pgettext_lazy("profile form", "First name"),
            "last_name": pgettext_lazy("profile form", "Last name"),
            "email": pgettext_lazy("profile form", "Email address"),
        }

        widgets = {
            "first_name": forms.TextInput(attrs={
                "class": INPUT_CLASSES,
                "placeholder": pgettext_lazy("profile form", "First name"),
                "autocomplete": "given-name",
            }),
            "last_name": forms.TextInput(attrs={
                "class": INPUT_CLASSES,
                "placeholder": pgettext_lazy("profile form", "Last name"),
                "autocomplete": "family-name",
            }),
            "email": forms.EmailInput(attrs={
                "class": INPUT_CLASSES,
                "placeholder": pgettext_lazy("profile form", "Email address"),
                "autocomplete": "email",
            }),
        }

    def clean_email(self):
        email = self.cleaned_data["email"]

        if (
            User.objects.exclude(pk=self.instance.pk)
            .filter(email__iexact=email)
            .exists()
        ):
            raise forms.ValidationError(
                pgettext_lazy("profile form", "A user with this email already exists.")
            )

        return email


class ProfileUpdateForm(forms.ModelForm):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["birthdate"] = build_birthdate_field(
            css_class=INPUT_CLASSES,
            label=pgettext_lazy("profile form", "Birth date"),
        )

    def clean_phone(self):
        phone = normalize_digits(self.cleaned_data.get("phone", ""))
        # A profile has no country; normalize Iranian-looking input without
        # imposing Iranian rules on international numbers.
        if phone.strip().replace(" ", "").replace("-", "").lstrip("+").startswith(("09", "98")):
            return normalize_iranian_mobile(phone)
        return phone

    class Meta:
        model = Profile

        fields = [
            "phone",
            "birthdate",
            "gender",
            "is_business",
            "company_name",
            "company_tax_id",
        ]

        labels = {
            "phone": pgettext_lazy("profile form", "Phone number"),
            "birthdate": pgettext_lazy("profile form", "Birth date"),
            "gender": pgettext_lazy("profile form", "Gender"),
            "is_business": pgettext_lazy("profile form", "Business customer"),
            "company_name": pgettext_lazy("profile form", "Company name"),
            "company_tax_id": pgettext_lazy("profile form", "Tax / VAT ID"),
        }

        widgets = {

            "phone": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": pgettext_lazy("profile form", "Phone number"),
                    "autocomplete": "tel",
                    "inputmode": "tel",
                    "dir": "ltr",
                }
            ),

            "birthdate": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": INPUT_CLASSES,
                }
            ),

            "gender": forms.Select(
                attrs={
                    "class": SELECT_CLASSES,
                }
            ),

            "is_business": forms.CheckboxInput(
                attrs={
                    "class": CHECKBOX_CLASSES,
                }
            ),

            "company_name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": pgettext_lazy("profile form", "Company name"),
                }
            ),

            "company_tax_id": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": pgettext_lazy("profile form", "Tax / VAT ID"),
                }
            ),
        }


# =====================================================================
# Address Book
# =====================================================================

class AddressForm(forms.ModelForm):

    def clean(self):
        cleaned = super().clean()
        country = cleaned.get("country", "")
        if is_iran(country):
            cleaned["country"] = "IR"
            try:
                cleaned["phone"] = normalize_iranian_mobile(cleaned.get("phone", ""))
            except ValidationError as error:
                self.add_error("phone", error)
            if cleaned.get("postal_code"):
                try:
                    cleaned["postal_code"] = normalize_iranian_postal_code(cleaned["postal_code"])
                except ValidationError as error:
                    self.add_error("postal_code", error)
        else:
            cleaned["phone"] = normalize_digits(cleaned.get("phone", ""))
            cleaned["postal_code"] = normalize_digits(cleaned.get("postal_code", ""))
        return cleaned

    class Meta:
        model = Address

        fields = [
            "label",
            "first_name",
            "last_name",
            "company_name",
            "phone",
            "country",
            "province",
            "city",
            "street_address",
            "postal_code",
            "is_default_shipping",
            "is_default_billing",
        ]

        labels = {
            "label": pgettext_lazy("address form", "Address label"),
            "first_name": pgettext_lazy("address form", "First name"),
            "last_name": pgettext_lazy("address form", "Last name"),
            "company_name": pgettext_lazy("address form", "Company name (optional)"),
            "phone": pgettext_lazy("address form", "Phone number"),
            "country": pgettext_lazy("address form", "Country"),
            "province": pgettext_lazy("address form", "Province / State"),
            "city": pgettext_lazy("address form", "City"),
            "street_address": pgettext_lazy("address form", "Street address"),
            "postal_code": pgettext_lazy("address form", "Postal code"),
            "is_default_shipping": pgettext_lazy("address form", "Use as default shipping address"),
            "is_default_billing": pgettext_lazy("address form", "Use as default billing address"),
        }

        widgets = {

            "label": forms.Select(
                attrs={
                    "class": SELECT_CLASSES,
                }
            ),

            "first_name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": pgettext_lazy("address form", "First name"),
                    "autocomplete": "given-name",
                }
            ),

            "last_name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": pgettext_lazy("address form", "Last name"),
                    "autocomplete": "family-name",
                }
            ),

            "company_name": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": pgettext_lazy("address form", "Company name (optional)"),
                    "autocomplete": "organization",
                }
            ),

            "phone": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": pgettext_lazy("address form", "Phone number"),
                    "autocomplete": "tel",
                    "inputmode": "tel",
                    "dir": "ltr",
                }
            ),

            "country": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": pgettext_lazy("address form", "Country"),
                    "autocomplete": "country",
                }
            ),

            "province": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": pgettext_lazy("address form", "Province / State"),
                    "autocomplete": "address-level1",
                }
            ),

            "city": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": pgettext_lazy("address form", "City"),
                    "autocomplete": "address-level2",
                }
            ),

            "street_address": forms.Textarea(
                attrs={
                    "rows": 4,
                    "class": TEXTAREA_CLASSES,
                    "placeholder": pgettext_lazy("address form", "Street address"),
                    "autocomplete": "street-address",
                }
            ),

            "postal_code": forms.TextInput(
                attrs={
                    "class": INPUT_CLASSES,
                    "placeholder": pgettext_lazy("address form", "Postal code"),
                    "autocomplete": "postal-code",
                    "inputmode": "numeric",
                    "dir": "ltr",
                }
            ),

            "is_default_shipping": forms.CheckboxInput(
                attrs={
                    "class": CHECKBOX_CLASSES,
                }
            ),

            "is_default_billing": forms.CheckboxInput(
                attrs={
                    "class": CHECKBOX_CLASSES,
                }
            ),
        }
