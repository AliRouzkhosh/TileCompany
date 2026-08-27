"""Read-only Tile legacy/generic shadow parity audit for F1B.4."""

from dataclasses import dataclass, field

from .catalog_backfill import (
    PRODUCT_LEGACY_VALUES,
    SPEC_BY_CODE,
    VARIANT_LEGACY_VALUES,
    inspect_tile_catalog,
)
from .catalog_domain import serialize_combination_signature
from .catalog_read import CatalogReadIntegrityError, read_product_details
from .models import (
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductVariant,
    VariantAttributeValue,
)


BLOCKING = "BLOCKING"
WARNING = "WARNING"


@dataclass(frozen=True)
class GenericReadAuditIssue:
    level: str
    code: str
    message: str


@dataclass
class GenericReadAuditReport:
    products_examined: int = 0
    variants_examined: int = 0
    product_attribute_parity_failures: int = 0
    variant_attribute_parity_failures: int = 0
    signature_consistency_failures: int = 0
    missing_generic_bindings_values: int = 0
    unexpected_generic_values: int = 0
    retired_disallowed_stored_values: int = 0
    known_operational_size_warnings: int = 0
    issues: list[GenericReadAuditIssue] = field(default_factory=list)

    @property
    def blocking_issues(self):
        return [issue for issue in self.issues if issue.level == BLOCKING]

    @property
    def warning_issues(self):
        return [issue for issue in self.issues if issue.level == WARNING]

    @property
    def usable(self):
        return not self.blocking_issues

    def add(self, level, code, message):
        self.issues.append(GenericReadAuditIssue(level, code, message))


def _legacy_product_codes(product):
    return {
        code: {row.slug for row in PRODUCT_LEGACY_VALUES[code](product)}
        for code in PRODUCT_LEGACY_VALUES
    }


def _option_is_available(binding, option_id, restrictions):
    binding_restrictions = restrictions.get(binding.pk, [])
    if not binding_restrictions:
        return True
    return any(
        restriction.option_id == option_id and restriction.is_active
        for restriction in binding_restrictions
    )


def run_generic_catalog_read_audit():
    report = GenericReadAuditReport()
    tile_type = ProductType.objects.filter(code="tile").first()
    if tile_type is None:
        report.missing_generic_bindings_values += 1
        report.add(BLOCKING, "missing_tile_product_type", "ProductType code 'tile' is missing.")
        return report

    products = list(
        Product.objects.filter(product_type=tile_type)
        .select_related("brand")
        .prefetch_related("designs", "materials", "usages")
        .order_by("id")
    )
    variants = list(
        ProductVariant.objects.filter(product__in=products)
        .select_related("color", "size", "finish", "product")
        .order_by("id")
    )
    report.products_examined = len(products)
    report.variants_examined = len(variants)

    bindings = list(
        ProductTypeAttribute.objects.filter(product_type=tile_type)
        .select_related("definition")
        .prefetch_related("option_restrictions")
    )
    binding_by_code = {binding.definition.code: binding for binding in bindings}
    missing_bindings = sorted(set(SPEC_BY_CODE) - binding_by_code.keys())
    unexpected_bindings = sorted(binding_by_code.keys() - set(SPEC_BY_CODE))
    for code in missing_bindings:
        report.missing_generic_bindings_values += 1
        report.add(BLOCKING, "missing_generic_binding", f"Tile binding {code!r} is missing.")
    for code in unexpected_bindings:
        report.unexpected_generic_values += 1
        report.add(BLOCKING, "unexpected_generic_binding", f"Unexpected Tile binding {code!r} exists.")

    product_value_rows = list(
        ProductAttributeValue.objects.filter(product_id__in=[product.pk for product in products])
        .select_related("binding__definition", "option_value")
        .order_by("product_id", "binding__sort_order", "sort_order", "id")
    )
    variant_value_rows = list(
        VariantAttributeValue.objects.filter(variant_id__in=[variant.pk for variant in variants])
        .select_related("binding__definition", "option_value")
        .order_by("variant_id", "binding__sort_order", "id")
    )
    product_values_by_owner = {}
    for value in product_value_rows:
        product_values_by_owner.setdefault(value.product_id, {}).setdefault(
            value.binding.definition.code, set()
        ).add(value.option_value.code if value.option_value_id else None)
    variant_values_by_owner = {}
    for value in variant_value_rows:
        variant_values_by_owner.setdefault(value.variant_id, {})[
            value.binding.definition.code
        ] = value.option_value.code if value.option_value_id else None

    try:
        read_product_details(products, language="en")
    except CatalogReadIntegrityError as exc:
        for message in exc.issues:
            if " signature " in message and "does not match" in message:
                # Counted precisely below from the complete batched value set.
                continue
            report.missing_generic_bindings_values += 1
            report.add(BLOCKING, "generic_read_integrity", message)

    for product in products:
        expected = _legacy_product_codes(product)
        actual = product_values_by_owner.get(product.pk, {})
        for code in PRODUCT_LEGACY_VALUES:
            expected_codes = expected[code]
            actual_codes = actual.get(code, set())
            if expected_codes != actual_codes:
                report.product_attribute_parity_failures += 1
                missing = sorted(expected_codes - actual_codes)
                unexpected = sorted(actual_codes - expected_codes)
                report.missing_generic_bindings_values += len(missing)
                report.unexpected_generic_values += len(unexpected)
                report.add(
                    BLOCKING,
                    "product_attribute_parity",
                    f"Product ID {product.pk} {code}: legacy={sorted(expected_codes)}, generic={sorted(actual_codes)}.",
                )
        extra_codes = sorted(set(actual) - set(PRODUCT_LEGACY_VALUES))
        if extra_codes:
            report.unexpected_generic_values += len(extra_codes)
            report.add(
                BLOCKING, "unexpected_product_values",
                f"Product ID {product.pk} has unexpected generic attributes {extra_codes}.",
            )

    dimension_codes = {
        binding.definition.code for binding in bindings if binding.is_variant_dimension
    }
    for legacy_variant in variants:
        expected = {
            code: getattr(VARIANT_LEGACY_VALUES[code](legacy_variant), "slug", None)
            for code in VARIANT_LEGACY_VALUES
        }
        actual_all = variant_values_by_owner.get(legacy_variant.pk, {})
        actual = {
            code: value for code, value in actual_all.items() if code in dimension_codes
        }
        if expected != actual:
            report.variant_attribute_parity_failures += 1
            report.missing_generic_bindings_values += sum(
                1 for code, value in expected.items() if value and code not in actual
            )
            report.unexpected_generic_values += sum(
                1 for code, value in actual.items() if code not in expected or expected[code] != value
            )
            report.add(
                BLOCKING,
                "variant_attribute_parity",
                f"Variant ID {legacy_variant.pk}: legacy={expected}, generic={actual}.",
            )
        extra_codes = sorted(set(actual_all) - dimension_codes)
        if extra_codes:
            report.unexpected_generic_values += len(extra_codes)
            report.add(
                BLOCKING, "unexpected_variant_values",
                f"Variant ID {legacy_variant.pk} has unexpected generic attributes {extra_codes}.",
            )
        expected_signature = serialize_combination_signature(actual.items())
        if legacy_variant.combination_signature != expected_signature:
            report.signature_consistency_failures += 1
            report.add(
                BLOCKING,
                "signature_consistency",
                f"Variant ID {legacy_variant.pk}: stored={legacy_variant.combination_signature!r}, expected={expected_signature!r}.",
            )

    restrictions = {
        binding.pk: list(binding.option_restrictions.all()) for binding in bindings
    }
    for owner_kind, owner_field, values in (
        ("Product", "product_id", product_value_rows),
        ("Variant", "variant_id", variant_value_rows),
    ):
        for value in values:
            if value.option_value_id and (
                not value.option_value.is_active
                or not _option_is_available(value.binding, value.option_value_id, restrictions)
            ):
                report.retired_disallowed_stored_values += 1
                report.add(
                    WARNING, "retired_disallowed_stored_value",
                    f"{owner_kind} ID {getattr(value, owner_field)} retains "
                    f"{value.binding.definition.code}={value.option_value.code}.",
                )

    reconciliation = inspect_tile_catalog(require_complete_generic=True)
    for issue in reconciliation.blocking_issues:
        report.add(BLOCKING, f"reconciliation_{issue.code}", issue.message)
    for issue in reconciliation.warning_issues:
        if issue.code == "size_dimension_mismatch":
            report.known_operational_size_warnings += 1
            report.add(WARNING, "known_size_dimension_mismatch", issue.message)

    return report
