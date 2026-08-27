# orders/forms.py

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.translation import gettext_lazy as _
from TileCompany.uploads import validate_raster_or_pdf

from .models import Order


# ==========================================================
# Shared Styles
# ==========================================================

INPUT_CLASSES = (
    "block w-full rounded-xl border border-stone-300 bg-white "
    "px-4 py-3 text-sm text-stone-900 placeholder:text-stone-400 "
    "shadow-sm transition "
    "focus:border-amber-500 focus:ring-4 focus:ring-amber-100 "
    "focus:outline-none"
)

SELECT_CLASSES = (
    "block w-full rounded-xl border border-stone-300 bg-white "
    "px-4 py-3 text-sm text-stone-900 "
    "shadow-sm transition "
    "focus:border-amber-500 focus:ring-4 focus:ring-amber-100 "
    "focus:outline-none"
)

TEXTAREA_CLASSES = (
    "block w-full rounded-xl border border-stone-300 bg-white "
    "px-4 py-3 text-sm text-stone-900 placeholder:text-stone-400 "
    "shadow-sm resize-y transition "
    "focus:border-amber-500 focus:ring-4 focus:ring-amber-100 "
    "focus:outline-none"
)

CHECKOUT_INPUT_CLASSES = (
    "block min-h-11 w-full rounded-control border border-border-strong bg-surface "
    "px-4 py-3 text-sm text-text-primary placeholder:text-text-muted shadow-surface "
    "transition focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand-subtle"
)

CHECKOUT_TEXTAREA_CLASSES = CHECKOUT_INPUT_CLASSES + " resize-y"

CHECKOUT_CHOICE_CLASSES = (
    "checkout-choice__control mt-0.5 h-5 w-5 shrink-0 cursor-pointer accent-brand"
)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if not files:
            return []
        return [super().clean(uploaded_file, initial) for uploaded_file in files]


# ==========================================================
# Shipping
# ==========================================================

class ShippingForm(forms.Form):

    address_id = forms.IntegerField(widget=forms.HiddenInput())

    shipping_method = forms.ChoiceField(
        choices=[],
        widget=forms.RadioSelect(attrs={"class": CHECKOUT_CHOICE_CLASSES}),
    )

    delivery_slot = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "class": CHECKOUT_INPUT_CLASSES,
                "placeholder": _("Preferred delivery time (optional)"),
                "aria-describedby": "delivery-slot-help",
            }
        ),
    )

    def __init__(self, *args, shipping_choices=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["shipping_method"].choices = (
            shipping_choices or []
        )

# ==========================================================
# Shipping Method
# ==========================================================

class ShippingMethodForm(forms.Form):
    """
    Selected shipping method.

    Choices are populated dynamically from selectors.py.
    """

    shipping_method = forms.ChoiceField(
        choices=[],
        widget=forms.Select(
            attrs={
                "class": SELECT_CLASSES,
            }
        ),
    )

    delivery_slot = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "class": INPUT_CLASSES,
                "placeholder": _("Preferred delivery time (optional)"),
            }
        ),
    )

    def __init__(self, *args, shipping_choices=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["shipping_method"].choices = (
            shipping_choices or []
        )


# ==========================================================
# Payment
# ==========================================================

class PaymentMethodForm(forms.Form):
    """
    Selected payment method.
    """

    payment_method = forms.ChoiceField(
        choices=[],
        widget=forms.RadioSelect(attrs={"class": CHECKOUT_CHOICE_CLASSES}),
    )

    coupon_code = forms.CharField(
        required=False,
        max_length=50,
        label=_("Coupon code"),
        widget=forms.TextInput(
            attrs={
                "class": CHECKOUT_INPUT_CLASSES,
                "placeholder": _("Coupon code"),
                "aria-describedby": "coupon-code-help",
            }
        ),
    )

    email_receipt_requested = forms.BooleanField(
        required=False,
        label=_("Send order receipt by email"),
        widget=forms.CheckboxInput(attrs={"class": CHECKOUT_CHOICE_CLASSES}),
    )

    def __init__(self, *args, payment_choices=None, customer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.customer = customer

        self.fields["payment_method"].choices = (
            payment_choices or []
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("email_receipt_requested"):
            email = (getattr(self.customer, "email", "") or "").strip()
            try:
                validate_email(email)
            except ValidationError:
                self.add_error(
                    "email_receipt_requested",
                    _("Add a valid email address to your account before requesting an email receipt."),
                )
            else:
                cleaned["receipt_email"] = email
        return cleaned


# ==========================================================
# Refund Request
# ==========================================================

class RefundRequestForm(forms.Form):
    """
    Customer refund request.
    """

    reason = forms.ChoiceField(
        choices=Order.RefundReason.choices,
        required=True,
        label=_("Refund reason"),
        widget=forms.Select(
            attrs={
                "class": SELECT_CLASSES,
            }
        ),
    )

    message = forms.CharField(
        required=True,
        label=_("Additional information"),
        help_text=_("Please describe the problem and any information that will help us review your request."),
        error_messages={
            "required": _("Additional information is required."),
        },
        widget=forms.Textarea(
            attrs={
                "class": TEXTAREA_CLASSES,
                "rows": 5,
                "placeholder": (
                    _("Please describe the problem and any information that will help us review your request.")
                ),
            }
        ),
    )

    attachments = MultipleFileField(
        required=False,
        label=_("Supporting files (PNG, JPG, JPEG, WEBP, or PDF)"),
        help_text=_("You may attach PNG, JPG, JPEG, WEBP, or PDF files as supporting evidence (maximum 10 MB per file)."),
        widget=MultipleFileInput(
            attrs={"multiple": True, "accept": ".jpg,.jpeg,.png,.webp,.pdf"}
        ),
    )

    def clean_attachments(self):
        files = self.cleaned_data.get("attachments", [])
        max_size = 10 * 1024 * 1024
        for uploaded_file in files:
            try:
                validate_raster_or_pdf(
                    uploaded_file,
                    max_image_bytes=max_size,
                    max_pdf_bytes=max_size,
                )
            except ValidationError:
                raise forms.ValidationError(_("Upload a valid JPG, PNG, WEBP, or PDF file."))
        return files
