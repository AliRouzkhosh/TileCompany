from decimal import Decimal

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator
from django.utils.translation import gettext_lazy as _

from orders.models import DeliveryMethodPolicy


INPUT = "w-full min-h-11 rounded-xl border border-stone-300 bg-white px-4 py-2.5 text-sm focus:border-stone-900 focus:outline-none focus:ring-2 focus:ring-stone-200"
CHECKBOX = "h-5 w-5 rounded border-stone-300"


class CommerceSettingsForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    tax_enabled = forms.BooleanField(required=False, label=_("Enable tax"), widget=forms.CheckboxInput(attrs={"class": CHECKBOX}))
    tax_rate = forms.DecimalField(label=_("Tax rate (%)"), min_value=Decimal("0"), max_value=Decimal("100"), max_digits=5, decimal_places=2, widget=forms.NumberInput(attrs={"class": INPUT, "step": "0.01"}))
    threshold_enabled = forms.BooleanField(required=False, label=_("Enable free-shipping threshold"), widget=forms.CheckboxInput(attrs={"class": CHECKBOX}))
    threshold_irt = forms.DecimalField(required=False, label=_("IRT threshold"), min_value=Decimal("0.01"), max_digits=14, decimal_places=2, widget=forms.NumberInput(attrs={"class": INPUT, "step": "1", "dir": "ltr"}))
    threshold_usd = forms.DecimalField(required=False, label=_("USD threshold"), min_value=Decimal("0.01"), max_digits=12, decimal_places=2, widget=forms.NumberInput(attrs={"class": INPUT, "step": "0.01", "dir": "ltr"}))

    def __init__(self, *args, policy=None, methods=None, **kwargs):
        self.policy = policy
        self.methods = methods or {}
        if not args and policy is not None and "initial" not in kwargs:
            initial = {
                "version": policy.version, "tax_enabled": policy.tax_enabled, "tax_rate": policy.tax_rate,
                "threshold_enabled": policy.free_shipping_threshold_enabled,
                "threshold_irt": policy.free_shipping_threshold_irt,
                "threshold_usd": policy.free_shipping_threshold_usd,
            }
            for code, method in self.methods.items():
                initial.update({f"{code}_mode": method.charge_mode, f"{code}_irt": method.fixed_amount_irt,
                                f"{code}_usd": method.fixed_amount_usd, f"{code}_eligible": method.eligible_for_free_shipping_threshold})
            kwargs["initial"] = initial
        super().__init__(*args, **kwargs)
        for code in ("standard", "express", "pickup"):
            self.fields[f"{code}_mode"] = forms.ChoiceField(label=_("Charge mode"), choices=DeliveryMethodPolicy.ChargeMode.choices, widget=forms.Select(attrs={"class": INPUT}))
            self.fields[f"{code}_irt"] = forms.DecimalField(required=False, label=_("IRT fixed amount"), min_value=0, max_digits=14, decimal_places=2, widget=forms.NumberInput(attrs={"class": INPUT, "step": "1", "dir": "ltr"}))
            self.fields[f"{code}_usd"] = forms.DecimalField(required=False, label=_("USD fixed amount"), min_value=0, max_digits=12, decimal_places=2, widget=forms.NumberInput(attrs={"class": INPUT, "step": "0.01", "dir": "ltr"}))
            self.fields[f"{code}_eligible"] = forms.BooleanField(required=False, label=_("Eligible for free-shipping threshold"), widget=forms.CheckboxInput(attrs={"class": CHECKBOX}))
            if self.initial:
                self.fields[f"{code}_mode"].initial = self.initial.get(f"{code}_mode")
                self.fields[f"{code}_irt"].initial = self.initial.get(f"{code}_irt")
                self.fields[f"{code}_usd"].initial = self.initial.get(f"{code}_usd")
                self.fields[f"{code}_eligible"].initial = self.initial.get(f"{code}_eligible")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("threshold_enabled"):
            if cleaned.get("threshold_irt") is None:
                self.add_error("threshold_irt", _("Enter the IRT threshold."))
            if cleaned.get("threshold_usd") is None:
                self.add_error("threshold_usd", _("Enter the USD threshold."))
        for code in ("standard", "express", "pickup"):
            if cleaned.get(f"{code}_mode") == DeliveryMethodPolicy.ChargeMode.FIXED:
                for suffix in ("irt", "usd"):
                    if cleaned.get(f"{code}_{suffix}") is None:
                        self.add_error(f"{code}_{suffix}", _("Enter a fixed amount for this currency."))
        return cleaned
