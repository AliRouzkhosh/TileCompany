"""
accounts/auth_forms.py

Styled authentication forms.

These extend Django's built-in authentication forms while applying
TileStore's design system.
"""

from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    SetPasswordForm,
    PasswordChangeForm,
)
from django.utils.translation import gettext_lazy as _


# =====================================================================
# Shared Widget Styles
# =====================================================================

INPUT_CLASSES = (
    "sf-auth-control"
)


# =====================================================================
# Login
# =====================================================================

class LoginForm(AuthenticationForm):

    error_messages = {
        "invalid_login": _("Please enter a correct username and password. Note that both fields may be case-sensitive."),
        "inactive": _("Please enter a correct username and password. Note that both fields may be case-sensitive."),
    }

    username = forms.CharField(
        label=_("Username"),
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": _("Username"),
                "autocomplete": "username",
                "autofocus": True,
            }
        )
    )

    password = forms.CharField(
        strip=False,
        label=_("Password"),
        widget=forms.PasswordInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": _("Password"),
                "autocomplete": "current-password",
            }
        ),
    )


# =====================================================================
# Password Reset
# =====================================================================

class StyledPasswordResetForm(PasswordResetForm):

    email = forms.EmailField(
        label=_("Email address"),
        widget=forms.EmailInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": _("Email address"),
                "autocomplete": "email",
            }
        )
    )


# =====================================================================
# Password Reset Confirmation
# =====================================================================

class StyledSetPasswordForm(SetPasswordForm):

    new_password1 = forms.CharField(
        strip=False,
        label=_("New password"),
        widget=forms.PasswordInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": _("New password"),
                "autocomplete": "new-password",
            }
        ),
    )

    new_password2 = forms.CharField(
        strip=False,
        label=_("Confirm new password"),
        widget=forms.PasswordInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": _("Confirm new password"),
                "autocomplete": "new-password",
            }
        ),
    )


# =====================================================================
# Password Change
# =====================================================================

class StyledPasswordChangeForm(PasswordChangeForm):

    old_password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": _("Current password"),
                "autocomplete": "current-password",
            }
        ),
    )

    new_password1 = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": _("New password"),
                "autocomplete": "new-password",
            }
        ),
    )

    new_password2 = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": _("Confirm new password"),
                "autocomplete": "new-password",
            }
        ),
    )
