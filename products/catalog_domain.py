from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


VALUE_FIELDS = (
    "option_value",
    "text_value",
    "integer_value",
    "decimal_value",
    "boolean_value",
)

VALUE_FIELD_BY_DATA_TYPE = {
    "option": "option_value",
    "text": "text_value",
    "integer": "integer_value",
    "decimal": "decimal_value",
    "boolean": "boolean_value",
}

ALLOWED_VALIDATION_KEYS = {
    "text": {"min_length", "max_length"},
    "integer": {"min", "max", "step"},
    "decimal": {"min", "max", "step"},
    "option": set(),
    "boolean": set(),
}


def exactly_one_typed_value_condition():
    """Return a DB-check expression requiring exactly one typed carrier."""
    branches = []
    for populated_field in VALUE_FIELDS:
        branch = Q(**{f"{populated_field}__isnull": False})
        for empty_field in VALUE_FIELDS:
            if empty_field != populated_field:
                branch &= Q(**{f"{empty_field}__isnull": True})
        branches.append(branch)

    condition = branches[0]
    for branch in branches[1:]:
        condition |= branch
    return condition


def _as_decimal(value, key):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({"validation_rules": _("%(key)s must be numeric.") % {"key": key}})


def validate_definition_rules(definition):
    rules = definition.validation_rules
    if not isinstance(rules, dict):
        raise ValidationError({"validation_rules": _("Validation rules must be an object.")})

    allowed = ALLOWED_VALIDATION_KEYS.get(definition.data_type, set())
    unknown = set(rules) - allowed
    if unknown:
        raise ValidationError(
            {"validation_rules": _("Unsupported validation keys: %(keys)s") % {"keys": ", ".join(sorted(unknown))}}
        )

    if definition.data_type == "text":
        for key in ("min_length", "max_length"):
            if key in rules and (not isinstance(rules[key], int) or isinstance(rules[key], bool) or rules[key] < 0):
                raise ValidationError({"validation_rules": _("%(key)s must be a non-negative integer.") % {"key": key}})
        if rules.get("min_length", 0) > rules.get("max_length", float("inf")):
            raise ValidationError({"validation_rules": _("min_length cannot exceed max_length.")})

    if definition.data_type in {"integer", "decimal"}:
        normalized = {key: _as_decimal(value, key) for key, value in rules.items()}
        if definition.data_type == "integer" and any(value != value.to_integral_value() for value in normalized.values()):
            raise ValidationError({"validation_rules": _("Integer rules must use whole numbers.")})
        if "step" in normalized and normalized["step"] <= 0:
            raise ValidationError({"validation_rules": _("step must be greater than zero.")})
        if "min" in normalized and "max" in normalized and normalized["min"] > normalized["max"]:
            raise ValidationError({"validation_rules": _("min cannot exceed max.")})


def validate_scalar_against_rules(definition, value):
    rules = definition.validation_rules
    if definition.data_type == "text":
        if "min_length" in rules and len(value) < rules["min_length"]:
            raise ValidationError({"text_value": _("Value is shorter than the configured minimum.")})
        if "max_length" in rules and len(value) > rules["max_length"]:
            raise ValidationError({"text_value": _("Value is longer than the configured maximum.")})
    elif definition.data_type in {"integer", "decimal"}:
        numeric_value = Decimal(str(value))
        minimum = Decimal(str(rules["min"])) if "min" in rules else None
        maximum = Decimal(str(rules["max"])) if "max" in rules else None
        step = Decimal(str(rules["step"])) if "step" in rules else None
        field = VALUE_FIELD_BY_DATA_TYPE[definition.data_type]
        if minimum is not None and numeric_value < minimum:
            raise ValidationError({field: _("Value is below the configured minimum.")})
        if maximum is not None and numeric_value > maximum:
            raise ValidationError({field: _("Value exceeds the configured maximum.")})
        if step is not None:
            origin = minimum or Decimal("0")
            if (numeric_value - origin) % step != 0:
                raise ValidationError({field: _("Value does not match the configured step.")})


def available_options_for_binding(binding):
    from .models import AttributeOption

    restrictions = binding.option_restrictions.all()
    if restrictions.exists():
        return AttributeOption.objects.filter(
            applicability_restrictions__binding=binding,
            applicability_restrictions__is_active=True,
            is_active=True,
        ).distinct().order_by(
            "applicability_restrictions__sort_order", "sort_order", "id"
        )
    return binding.definition.options.filter(is_active=True).order_by("sort_order", "id")


def validate_option_available_for_new_assignment(value_object, option):
    binding = value_object.binding
    previous_option_id = None
    if value_object.pk:
        previous_option_id = type(value_object).objects.filter(pk=value_object.pk).values_list(
            "option_value_id", flat=True
        ).first()

    if previous_option_id == option.pk:
        return
    if not option.is_active or not available_options_for_binding(binding).filter(pk=option.pk).exists():
        raise ValidationError({"option_value": _("This option is not available for the product type schema.")})


def validate_typed_value(value_object):
    if not value_object.binding_id:
        return
    definition = value_object.binding.definition
    populated = [field for field in VALUE_FIELDS if getattr(value_object, field) is not None]
    if len(populated) != 1:
        raise ValidationError(_("Exactly one typed value must be provided."))

    expected_field = VALUE_FIELD_BY_DATA_TYPE[definition.data_type]
    if populated[0] != expected_field:
        raise ValidationError({populated[0]: _("Value carrier does not match the attribute data type.")})

    value = getattr(value_object, expected_field)
    if definition.data_type == "option":
        if value.definition_id != definition.pk:
            raise ValidationError({"option_value": _("Option belongs to a different attribute definition.")})
        validate_option_available_for_new_assignment(value_object, value)
    else:
        validate_scalar_against_rules(definition, value)


def validate_primary_category_membership(product, category=None):
    category = category if category is not None else product.primary_category
    if category is None:
        return
    if not product.pk or not product.categories.filter(pk=category.pk).exists():
        raise ValidationError({"primary_category": _("Primary category must also be assigned to the product.")})


def serialize_combination_signature(pairs):
    pairs = list(pairs)
    if not pairs:
        return "__base__"
    if any(not definition_code or not value_code for definition_code, value_code in pairs):
        raise ValidationError(_("Combination identity codes cannot be blank."))
    definition_codes = [definition_code for definition_code, _ in pairs]
    if len(definition_codes) != len(set(definition_codes)):
        raise ValidationError(_("A variant dimension cannot appear more than once."))
    return "|".join(
        f"{definition_code}={value_code}"
        for definition_code, value_code in sorted(pairs, key=lambda pair: pair[0])
    )


def calculate_variant_combination_signature(variant):
    if not variant.pk:
        raise ValidationError(_("Save the variant before calculating its combination identity."))
    if not variant.product.product_type_id:
        raise ValidationError(_("The product must have a product type before calculating a combination identity."))

    dimensions = variant.product.product_type.attribute_bindings.filter(
        scope="variant", is_variant_dimension=True, is_active=True
    ).select_related("definition").order_by("definition__code")
    if not dimensions.exists():
        return "__base__"

    pairs = []
    for binding in dimensions:
        try:
            value = variant.attribute_values.select_related("option_value").get(binding=binding)
        except variant.attribute_values.model.DoesNotExist:
            raise ValidationError(_("Every configured variant dimension must have a value."))
        if value.option_value_id is None:
            raise ValidationError(_("Variant dimensions must use controlled options."))
        pairs.append((binding.definition.code, value.option_value.code))
    return serialize_combination_signature(pairs)


def validate_variant_combination_signature(variant):
    expected = calculate_variant_combination_signature(variant)
    if variant.combination_signature != expected:
        raise ValidationError(
            {"combination_signature": _("Combination signature does not match the configured dimension values.")}
        )
