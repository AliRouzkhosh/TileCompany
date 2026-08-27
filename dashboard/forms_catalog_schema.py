from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from products.models import (
    AttributeDefinition,
    AttributeOption,
    Category,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
)


INPUT_CLASS = "w-full rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm focus:border-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-200"
CHECKBOX_CLASS = "h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-900"


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", CHECKBOX_CLASS)
            else:
                field.widget.attrs.setdefault("class", INPUT_CLASS)


class ProductTypeForm(StyledModelForm):
    class Meta:
        model = ProductType
        fields = ("code", "name", "name_fa", "sort_order", "is_active")
        help_texts = {"code": _("Stable machine identity. It cannot change after reference.")}


class AttributeDefinitionForm(StyledModelForm):
    numeric_min = forms.DecimalField(required=False, label=_("Minimum"))
    numeric_max = forms.DecimalField(required=False, label=_("Maximum"))
    numeric_step = forms.DecimalField(required=False, min_value=0, label=_("Step"))
    text_min_length = forms.IntegerField(required=False, min_value=0, label=_("Minimum length"))
    text_max_length = forms.IntegerField(required=False, min_value=0, label=_("Maximum length"))

    class Meta:
        model = AttributeDefinition
        fields = ("code", "name", "name_fa", "data_type", "unit", "is_active")
        help_texts = {"code": _("Globally unique stable machine identity.")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rules = self.instance.validation_rules if self.instance.pk else {}
        self.initial.update({
            "numeric_min": rules.get("min"),
            "numeric_max": rules.get("max"),
            "numeric_step": rules.get("step"),
            "text_min_length": rules.get("min_length"),
            "text_max_length": rules.get("max_length"),
        })

    def clean(self):
        cleaned = super().clean()
        data_type = cleaned.get("data_type")
        if cleaned.get("unit") and data_type not in {"integer", "decimal"}:
            self.add_error("unit", _("Units are supported only for numeric attributes."))
        if data_type in {"integer", "decimal"}:
            mapping = (("numeric_min", "min"), ("numeric_max", "max"), ("numeric_step", "step"))
        elif data_type == "text":
            mapping = (("text_min_length", "min_length"), ("text_max_length", "max_length"))
        else:
            mapping = ()
        rules = {target: cleaned[source] for source, target in mapping if cleaned.get(source) is not None}
        if data_type == "integer":
            for key, value in list(rules.items()):
                if value != value.to_integral_value():
                    self.add_error(f"numeric_{key}", _("Integer validation rules must be whole numbers."))
                else:
                    rules[key] = int(value)
        elif data_type == "decimal":
            rules = {key: str(value) for key, value in rules.items()}
        self.instance.validation_rules = rules
        return cleaned


class AttributeOptionForm(StyledModelForm):
    presentation_color = forms.RegexField(
        required=False,
        regex=r"^#[0-9A-Fa-f]{6}$",
        label=_("Presentation color"),
        help_text=_("Optional UI-only hexadecimal color, for example #1f2937."),
    )

    class Meta:
        model = AttributeOption
        fields = ("code", "name", "name_fa", "sort_order", "is_active")
        help_texts = {"code": _("Stable within this Attribute. It cannot change after reference.")}

    def __init__(self, *args, definition=None, **kwargs):
        super().__init__(*args, **kwargs)
        if definition is not None:
            self.instance.definition = definition
        if self.instance.pk:
            self.initial["presentation_color"] = self.instance.presentation_metadata.get("color", "")

    def clean(self):
        cleaned = super().clean()
        color = cleaned.get("presentation_color")
        self.instance.presentation_metadata = {"color": color} if color else {}
        return cleaned


class ProductTypeAttributeForm(StyledModelForm):
    class Meta:
        model = ProductTypeAttribute
        fields = (
            "definition", "scope", "cardinality", "is_required", "is_variant_dimension",
            "sort_order", "is_filterable", "facet_mode", "is_searchable",
            "show_on_storefront", "show_on_product_card", "show_in_specifications",
            "include_in_purchase_snapshot", "is_active",
        )

    def __init__(self, *args, product_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        if product_type is not None:
            self.instance.product_type = product_type
        self.fields["definition"].queryset = AttributeDefinition.objects.order_by("name", "id")


class ProductTypeAttributeOptionForm(StyledModelForm):
    class Meta:
        model = ProductTypeAttributeOption
        fields = ("option", "sort_order", "is_active")

    def __init__(self, *args, binding=None, **kwargs):
        super().__init__(*args, **kwargs)
        if binding is not None:
            self.instance.binding = binding
            self.fields["option"].queryset = binding.definition.options.order_by("sort_order", "name", "id")


class CategorySchemaForm(StyledModelForm):
    class Meta:
        model = Category
        fields = (
            "name", "name_fa", "slug", "parent", "sort_order", "is_active", "show_in_navigation"
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Category.objects.order_by("sort_order", "name", "id")
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = queryset
