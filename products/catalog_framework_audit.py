"""Read-only, multi-domain integrity audit for the generic catalog framework."""

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db.models import Count

from .catalog_domain import calculate_variant_combination_signature
from .models import (
    AttributeDefinition,
    AttributeOption,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    VariantAttributeValue,
)


INFO = "INFO"
WARNING = "WARNING"
BLOCKING = "BLOCKING"


@dataclass(frozen=True)
class FrameworkAuditIssue:
    level: str
    code: str
    message: str


@dataclass
class FrameworkAuditReport:
    product_types_examined: int = 0
    definitions_examined: int = 0
    bindings_examined: int = 0
    products_examined: int = 0
    variants_examined: int = 0
    issues: list[FrameworkAuditIssue] = field(default_factory=list)

    def add(self, level, code, message):
        self.issues.append(FrameworkAuditIssue(level, code, message))

    @property
    def blocking_issues(self):
        return [issue for issue in self.issues if issue.level == BLOCKING]

    @property
    def warning_issues(self):
        return [issue for issue in self.issues if issue.level == WARNING]


def _validation_messages(error):
    if hasattr(error, "message_dict"):
        return "; ".join(
            f"{field}: {', '.join(str(item) for item in messages)}"
            for field, messages in error.message_dict.items()
        )
    return "; ".join(str(item) for item in error.messages)


def _audit_clean(report, objects, prefix):
    for instance in objects:
        try:
            instance.full_clean()
        except ValidationError as error:
            report.add(
                BLOCKING,
                f"invalid_{prefix}",
                f"{prefix} ID {instance.pk}: {_validation_messages(error)}",
            )


def _option_allowed(binding, option):
    memberships = list(binding.option_restrictions.all())
    if not memberships:
        return option.is_active
    return option.is_active and any(
        row.option_id == option.pk and row.is_active for row in memberships
    )


def run_catalog_framework_audit():
    report = FrameworkAuditReport()
    product_types = list(ProductType.objects.order_by("id"))
    definitions = list(AttributeDefinition.objects.order_by("id"))
    bindings = list(
        ProductTypeAttribute.objects.select_related("product_type", "definition")
        .prefetch_related("option_restrictions")
        .order_by("id")
    )
    options = list(AttributeOption.objects.select_related("definition").order_by("id"))
    memberships = list(
        ProductTypeAttributeOption.objects.select_related(
            "binding__definition", "binding__product_type", "option__definition"
        ).order_by("id")
    )
    products = list(Product.objects.select_related("product_type").order_by("id"))
    variants = list(
        ProductVariant.objects.select_related("product__product_type")
        .prefetch_related("attribute_values__binding__definition", "attribute_values__option_value")
        .order_by("id")
    )
    product_values = list(
        ProductAttributeValue.objects.select_related(
            "product__product_type", "binding__product_type", "binding__definition", "option_value"
        ).order_by("id")
    )
    variant_values = list(
        VariantAttributeValue.objects.select_related(
            "variant__product__product_type", "binding__product_type", "binding__definition", "option_value"
        ).order_by("id")
    )

    report.product_types_examined = len(product_types)
    report.definitions_examined = len(definitions)
    report.bindings_examined = len(bindings)
    report.products_examined = len(products)
    report.variants_examined = len(variants)

    _audit_clean(report, product_types, "product_type")
    _audit_clean(report, definitions, "definition")
    _audit_clean(report, options, "option")
    _audit_clean(report, bindings, "binding")
    _audit_clean(report, memberships, "membership")
    _audit_clean(report, product_values, "product_value")
    _audit_clean(report, variant_values, "variant_value")

    binding_by_id = {binding.pk: binding for binding in bindings}

    for product in products:
        if product.product_type_id is None:
            report.add(
                BLOCKING, "product_type_missing",
                f"Product ID {product.pk} SKU {product.sku} has no ProductType.",
            )
        elif not product.product_type.is_active:
            report.add(
                WARNING, "inactive_product_type_in_use",
                f"Product ID {product.pk} uses inactive ProductType {product.product_type.code}.",
            )

    product_value_bindings = {}
    for value in product_values:
        product_value_bindings.setdefault(value.product_id, set()).add(value.binding_id)
    required_product_bindings = {}
    for binding in bindings:
        if (
            binding.is_active
            and binding.scope == ProductTypeAttribute.Scope.PRODUCT
            and binding.is_required
        ):
            required_product_bindings.setdefault(binding.product_type_id, set()).add(binding.pk)
        if binding.is_active and (
            not binding.product_type.is_active or not binding.definition.is_active
        ):
            report.add(
                WARNING,
                "active_binding_uses_inactive_schema",
                f"Active binding ID {binding.pk} uses an inactive ProductType or definition.",
            )
    for product in products:
        missing = required_product_bindings.get(product.product_type_id, set()) - product_value_bindings.get(product.pk, set())
        if missing:
            codes = sorted(binding_by_id[pk].definition.code for pk in missing)
            report.add(
                BLOCKING,
                "missing_required_product_attribute",
                f"Product ID {product.pk} SKU {product.sku} is missing required attributes {codes}.",
            )

    for owner, values in (("Product", product_values), ("Variant", variant_values)):
        for value in values:
            binding = binding_by_id.get(value.binding_id)
            if binding is None:
                report.add(BLOCKING, "orphan_value_binding", f"{owner} value ID {value.pk} has no binding.")
                continue
            if value.option_value_id and not _option_allowed(binding, value.option_value):
                report.add(
                    WARNING,
                    "retired_or_disallowed_stored_value",
                    f"{owner} value ID {value.pk} retains {binding.definition.code}={value.option_value.code}.",
                )

    required_dimensions = {}
    for binding in bindings:
        if binding.is_active and binding.is_variant_dimension and binding.is_required:
            required_dimensions.setdefault(binding.product_type_id, set()).add(binding.pk)
    for variant in variants:
        value_binding_ids = {value.binding_id for value in variant.attribute_values.all()}
        missing = required_dimensions.get(variant.product.product_type_id, set()) - value_binding_ids
        if variant.is_active and missing:
            codes = sorted(binding_by_id[pk].definition.code for pk in missing)
            report.add(
                BLOCKING,
                "missing_required_dimension",
                f"Active Variant ID {variant.pk} SKU {variant.sku} is missing dimensions {codes}.",
            )
        if variant.combination_signature is not None or not missing:
            try:
                expected = calculate_variant_combination_signature(variant)
            except ValidationError as error:
                report.add(
                    BLOCKING, "signature_source_invalid",
                    f"Variant ID {variant.pk}: {_validation_messages(error)}",
                )
            else:
                if variant.combination_signature != expected:
                    report.add(
                        BLOCKING,
                        "signature_mismatch",
                        f"Variant ID {variant.pk} stores {variant.combination_signature!r}; expected {expected!r}.",
                    )

    duplicates = (
        ProductVariant.objects.exclude(combination_signature__isnull=True)
        .values("product_id", "combination_signature")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
    )
    for duplicate in duplicates:
        report.add(
            BLOCKING,
            "duplicate_combination",
            f"Product ID {duplicate['product_id']} has {duplicate['count']} variants with signature {duplicate['combination_signature']!r}.",
        )

    report.add(
        INFO,
        "audit_complete",
        f"Inspected {len(product_types)} ProductTypes, {len(products)} Products, and {len(variants)} Variants.",
    )
    return report
