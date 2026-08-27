from decimal import Decimal

from django import forms
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _

from products.models import AttributeDefinition, Brand, Product, ProductTypeAttribute

from .forms_catalog_schema import CHECKBOX_CLASS, INPUT_CLASS


class GenericProductForm(forms.ModelForm):
    category_ids = forms.MultipleChoiceField(
        required=False, label=_("Categories"), widget=forms.SelectMultiple(attrs={"size": 10})
    )
    primary_category_id = forms.TypedChoiceField(
        required=False, coerce=int, empty_value=None, label=_("Primary category")
    )

    class Meta:
        model = Product
        fields = (
            "name", "name_fa", "slug", "sku", "description", "description_fa",
            "brand", "image", "is_featured",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "description_fa": forms.Textarea(attrs={"rows": 5}),
        }
        labels = {
            "name": _("English name"), "name_fa": _("Persian name"),
            "description": _("English description"), "description_fa": _("Persian description"),
        }

    def __init__(self, *args, product_type, schema_context, **kwargs):
        super().__init__(*args, **kwargs)
        self.product_type = product_type
        self.schema_context = schema_context
        self.instance.product_type = product_type
        self.fields["brand"].queryset = Brand.objects.order_by("name", "id")
        self.fields["brand"].label_from_instance = lambda brand: (
            f"{brand.name} — {brand.name_fa}" if brand.name_fa.strip() else brand.name
        )
        category_choices = [(str(pk), label) for pk, label in schema_context.category_choices]
        self.fields["category_ids"].choices = category_choices
        self.fields["primary_category_id"].choices = [("", _("No primary category")), *category_choices]
        if self.instance.pk and not self.is_bound:
            self.initial["category_ids"] = [str(pk) for pk in self.instance.categories.values_list("pk", flat=True)]
            self.initial["primary_category_id"] = self.instance.primary_category_id or ""

        self.dynamic_field_names = []
        for binding in schema_context.bindings:
            name = self.dynamic_name(binding)
            self.fields[name] = self._build_dynamic_field(binding)
            self.dynamic_field_names.append(name)
            if not self.is_bound:
                self.initial[name] = self._initial_dynamic_value(binding)

        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", CHECKBOX_CLASS)
            else:
                field.widget.attrs.setdefault("class", INPUT_CLASS)
        for name in ("name", "slug", "sku", "description"):
            self.fields[name].widget.attrs.setdefault("dir", "ltr")
        for name in ("name_fa", "description_fa"):
            self.fields[name].widget.attrs.setdefault("dir", "rtl")

    @staticmethod
    def dynamic_name(binding):
        return f"attribute_{binding.pk}"

    def _label(self, binding):
        label = (
            binding.definition.name_fa
            if (get_language() or "").lower().startswith("fa") and binding.definition.name_fa
            else binding.definition.name
        )
        if binding.definition.unit:
            label = f"{label} ({binding.definition.unit})"
        return label

    def _build_dynamic_field(self, binding):
        definition = binding.definition
        common = {"label": self._label(binding), "required": binding.is_required}
        rules = definition.validation_rules
        if definition.data_type == AttributeDefinition.DataType.OPTION:
            choices = []
            for option in self.schema_context.options_by_binding.get(binding.pk, ()):
                label = (
                    option.name_fa
                    if (get_language() or "").lower().startswith("fa") and option.name_fa
                    else option.name
                )
                if option.pk in self.schema_context.unavailable_current_option_ids:
                    label = _("%(label)s (retired/unavailable — current value)") % {"label": label}
                choices.append((str(option.pk), label))
            if binding.cardinality == ProductTypeAttribute.Cardinality.MULTIPLE:
                return forms.MultipleChoiceField(choices=choices, **common)
            return forms.TypedChoiceField(
                choices=[("", _("Select…")), *choices], coerce=int, empty_value=None, **common
            )
        if definition.data_type == AttributeDefinition.DataType.TEXT:
            max_length = rules.get("max_length")
            widget = forms.Textarea(attrs={"rows": 3}) if max_length is None or max_length > 255 else forms.TextInput()
            return forms.CharField(
                min_length=rules.get("min_length"), max_length=max_length, widget=widget, **common
            )
        if definition.data_type == AttributeDefinition.DataType.INTEGER:
            return forms.IntegerField(
                min_value=rules.get("min"), max_value=rules.get("max"),
                step_size=rules.get("step"), **common
            )
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
                coerce=lambda value: {"true": True, "false": False}.get(value),
                empty_value=None,
                **common,
            )
        raise ValueError(f"Unsupported attribute type {definition.data_type!r}")

    def _initial_dynamic_value(self, binding):
        rows = self.schema_context.values_by_binding.get(binding.pk, ())
        if not rows:
            return None
        definition = binding.definition
        if definition.data_type == AttributeDefinition.DataType.OPTION:
            ids = [str(row.option_value_id) for row in rows]
            return ids if binding.cardinality == ProductTypeAttribute.Cardinality.MULTIPLE else ids[0]
        field = {
            "text": "text_value", "integer": "integer_value", "decimal": "decimal_value",
            "boolean": "boolean_value",
        }[definition.data_type]
        value = getattr(rows[0], field)
        if definition.data_type == AttributeDefinition.DataType.BOOLEAN:
            return "true" if value is True else "false"
        return value

    def clean_sku(self):
        sku = self.cleaned_data.get("sku")
        return sku.strip().upper() if sku else sku

    def clean(self):
        cleaned = super().clean()
        categories = {int(value) for value in cleaned.get("category_ids", [])}
        primary = cleaned.get("primary_category_id")
        if primary is not None and primary not in categories:
            self.add_error("primary_category_id", _("Primary category must also be selected in Categories."))
        return cleaned

    @property
    def core_bound_fields(self):
        names = (
            "name", "name_fa", "slug", "sku", "description", "description_fa",
            "category_ids", "primary_category_id", "brand", "image", "is_featured",
        )
        return tuple(self[name] for name in names)

    @property
    def dynamic_bound_fields(self):
        return tuple(self[name] for name in self.dynamic_field_names)

    def desired_attribute_values(self):
        return {
            binding.pk: self.cleaned_data[self.dynamic_name(binding)]
            for binding in self.schema_context.bindings
        }
