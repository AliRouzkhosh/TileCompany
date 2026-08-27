from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from products.currency import Currency
from products.models import AttributeDefinition, ProductTypeAttribute, ProductVariant, VariantPrice

from .forms_catalog_schema import CHECKBOX_CLASS, INPUT_CLASS


class GenericVariantForm(forms.ModelForm):
    class Meta:
        model = ProductVariant
        fields = ("sku", "price_unit", "stock", "low_stock_threshold", "sort_order")

    def __init__(self, *args, product, schema_context, can_change_inventory=True, can_change_pricing=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_change_inventory = can_change_inventory
        self.can_change_pricing = can_change_pricing
        self.product = product
        self.schema_context = schema_context
        self.instance.product = product
        self.dimension_field_names = []
        self.specification_field_names = []
        for binding in schema_context.bindings:
            name = self.dynamic_name(binding)
            self.fields[name] = self._build_dynamic_field(binding)
            target = self.dimension_field_names if binding.is_variant_dimension else self.specification_field_names
            target.append(name)
            if not self.is_bound:
                self.initial[name] = self._initial_value(binding)
        for currency in Currency:
            prefix = currency.value.lower()
            label = _("Toman") if currency == Currency.TOMAN else currency.label
            self.fields[f"{prefix}_amount"] = forms.DecimalField(
                label=_("%(currency)s selling price") % {"currency": label}, required=False,
                max_digits=14, decimal_places=2,
            )
            self.fields[f"{prefix}_compare_at_amount"] = forms.DecimalField(
                label=_("%(currency)s compare-at price") % {"currency": label}, required=False,
                max_digits=14, decimal_places=2,
            )
        if self.instance.pk and not self.is_bound:
            for price in self.instance.prices.all():
                prefix = price.currency.lower()
                self.initial[f"{prefix}_amount"] = price.amount
                self.initial[f"{prefix}_compare_at_amount"] = price.compare_at_amount
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", CHECKBOX_CLASS)
            else:
                field.widget.attrs.setdefault("class", INPUT_CLASS)
        self.fields["sku"].widget.attrs.setdefault("dir", "ltr")
        if not can_change_inventory:
            self.fields["stock"].disabled = True
            self.fields["low_stock_threshold"].disabled = True
        if not can_change_pricing:
            for currency in Currency:
                prefix = currency.value.lower()
                self.fields[f"{prefix}_amount"].disabled = True
                self.fields[f"{prefix}_compare_at_amount"].disabled = True

    @staticmethod
    def dynamic_name(binding):
        return f"attribute_{binding.pk}"

    def _label(self, binding):
        definition = binding.definition
        label = definition.name_fa if (get_language() or "").startswith("fa") and definition.name_fa else definition.name
        return f"{label} ({definition.unit})" if definition.unit else label

    def _build_dynamic_field(self, binding):
        definition = binding.definition
        # All generic Variants are drafts in F2C; required dimensions may be omitted temporarily.
        common = {"label": self._label(binding), "required": False}
        rules = definition.validation_rules
        if definition.data_type == AttributeDefinition.DataType.OPTION:
            choices = []
            for option in self.schema_context.options_by_binding.get(binding.pk, ()):
                label = option.name_fa if (get_language() or "").startswith("fa") and option.name_fa else option.name
                if option.pk in self.schema_context.unavailable_current_option_ids:
                    label = _("%(label)s (retired/unavailable — current value)") % {"label": label}
                choices.append((str(option.pk), label))
            return forms.TypedChoiceField(
                choices=[("", _("Select…")), *choices], coerce=int, empty_value=None, **common
            )
        if definition.data_type == AttributeDefinition.DataType.TEXT:
            max_length = rules.get("max_length")
            return forms.CharField(
                min_length=rules.get("min_length"), max_length=max_length,
                widget=forms.Textarea(attrs={"rows": 3}) if not max_length or max_length > 255 else forms.TextInput(),
                **common,
            )
        if definition.data_type == AttributeDefinition.DataType.INTEGER:
            return forms.IntegerField(min_value=rules.get("min"), max_value=rules.get("max"), step_size=rules.get("step"), **common)
        if definition.data_type == AttributeDefinition.DataType.DECIMAL:
            return forms.DecimalField(
                max_digits=18, decimal_places=6,
                min_value=Decimal(str(rules["min"])) if "min" in rules else None,
                max_value=Decimal(str(rules["max"])) if "max" in rules else None,
                step_size=Decimal(str(rules["step"])) if "step" in rules else None,
                **common,
            )
        if definition.data_type == AttributeDefinition.DataType.BOOLEAN:
            return forms.TypedChoiceField(
                choices=(("", _("Not set")), ("true", _("Yes")), ("false", _("No"))),
                coerce=lambda value: {"true": True, "false": False}.get(value), empty_value=None, **common,
            )
        raise ValueError(f"Unsupported Variant attribute type {definition.data_type!r}")

    def _initial_value(self, binding):
        rows = self.schema_context.values_by_binding.get(binding.pk, ())
        if not rows:
            return None
        row = rows[0]
        if binding.definition.data_type == "option":
            return row.option_value_id
        field = {"text": "text_value", "integer": "integer_value", "decimal": "decimal_value", "boolean": "boolean_value"}[binding.definition.data_type]
        value = getattr(row, field)
        return "true" if value is True else "false" if value is False else value

    def clean_sku(self):
        return self.cleaned_data["sku"].strip().upper()

    def clean(self):
        cleaned = super().clean()

        if not self.can_change_inventory:
            for field_name in ("stock", "low_stock_threshold"):
                if self.is_bound and field_name in self.data:
                    self.add_error(
                        field_name,
                        _("You do not have permission to change this field."),
                    )

        if not self.can_change_pricing:
            for currency in Currency:
                prefix = currency.value.lower()
                for field_name in (f"{prefix}_amount", f"{prefix}_compare_at_amount"):
                    if self.is_bound and field_name in self.data:
                        self.add_error(
                            field_name,
                            _("You do not have permission to change this field."),
                        )

        for currency in Currency:
            prefix = currency.value.lower()
            amount = cleaned.get(f"{prefix}_amount")
            compare = cleaned.get(f"{prefix}_compare_at_amount")
            if compare is not None and amount is None:
                self.add_error(f"{prefix}_amount", _("A selling price is required when compare-at price is set."))
            elif amount is not None:
                candidate = VariantPrice(variant=self.instance if self.instance.pk else None, currency=currency, amount=amount, compare_at_amount=compare)
                try:
                    candidate.clean()
                except ValidationError as error:
                    for field, messages in error.message_dict.items():
                        self.add_error(f"{prefix}_{field}", messages)
        return cleaned

    @property
    def dimension_bound_fields(self):
        return tuple(self[name] for name in self.dimension_field_names)

    @property
    def specification_bound_fields(self):
        return tuple(self[name] for name in self.specification_field_names)

    def desired_attribute_values(self):
        return {binding.pk: self.cleaned_data[self.dynamic_name(binding)] for binding in self.schema_context.bindings}

    def currency_price_data(self):
        for currency in Currency:
            prefix = currency.value.lower()
            yield currency, self.cleaned_data.get(f"{prefix}_amount"), self.cleaned_data.get(f"{prefix}_compare_at_amount")
