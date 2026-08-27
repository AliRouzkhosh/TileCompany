from dataclasses import dataclass, field
from typing import Callable

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .catalog_domain import (
    calculate_variant_combination_signature,
    serialize_combination_signature,
)
from .models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
    Category,
    Color,
    Design,
    Finish,
    Material,
    Product,
    ProductAttributeValue,
    ProductImage,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    Size,
    Usage,
    VariantAttributeValue,
    VariantImage,
    VariantPrice,
)


INFO = "INFO"
WARNING = "WARNING"
BLOCKING = "BLOCKING"


@dataclass(frozen=True)
class TileAttributeSpec:
    code: str
    name: str
    name_fa: str
    legacy_model: type
    scope: str
    cardinality: str
    sort_order: int
    is_variant_dimension: bool = False
    is_required: bool = False
    show_on_product_card: bool = False
    include_in_purchase_snapshot: bool = False

    @property
    def binding_values(self):
        return {
            "scope": self.scope,
            "cardinality": self.cardinality,
            "is_required": self.is_required,
            "is_variant_dimension": self.is_variant_dimension,
            "is_filterable": True,
            "facet_mode": ProductTypeAttribute.FacetMode.TERMS,
            "is_searchable": True,
            "show_on_storefront": True,
            "show_on_product_card": self.show_on_product_card,
            "show_in_specifications": True,
            "include_in_purchase_snapshot": self.include_in_purchase_snapshot,
            "sort_order": self.sort_order,
            "is_active": True,
        }


TILE_PRODUCT_TYPE = {
    "code": "tile",
    "name": "Tile",
    "name_fa": "کاشی",
    "is_active": True,
    "sort_order": 0,
}

TILE_ATTRIBUTE_SPECS = (
    TileAttributeSpec(
        "brand", "Brand", "برند", Brand, "product", "single", 1,
        show_on_product_card=True,
    ),
    TileAttributeSpec(
        "material", "Material", "جنس", Material, "product", "multiple", 2,
        show_on_product_card=True, include_in_purchase_snapshot=True,
    ),
    TileAttributeSpec("design", "Design", "طرح", Design, "product", "multiple", 3),
    TileAttributeSpec("usage", "Usage", "کاربرد", Usage, "product", "multiple", 4),
    TileAttributeSpec(
        "color", "Color", "رنگ", Color, "variant", "single", 1,
        is_variant_dimension=True, is_required=True,
        include_in_purchase_snapshot=True,
    ),
    TileAttributeSpec(
        "size", "Size", "اندازه", Size, "variant", "single", 2,
        is_variant_dimension=True, is_required=True,
        include_in_purchase_snapshot=True,
    ),
    TileAttributeSpec(
        "finish", "Finish", "سطح", Finish, "variant", "single", 3,
        is_variant_dimension=True, is_required=True,
        include_in_purchase_snapshot=True,
    ),
)

SPEC_BY_CODE = {spec.code: spec for spec in TILE_ATTRIBUTE_SPECS}
PRODUCT_LEGACY_VALUES = {
    "brand": lambda product: [] if product.brand_id is None else [product.brand],
    "design": lambda product: list(product.designs.all()),
    "material": lambda product: list(product.materials.all()),
    "usage": lambda product: list(product.usages.all()),
}
VARIANT_LEGACY_VALUES = {
    "color": lambda variant: variant.color,
    "size": lambda variant: variant.size,
    "finish": lambda variant: variant.finish,
}


@dataclass(frozen=True)
class ReconciliationIssue:
    level: str
    code: str
    message: str


@dataclass
class TileReconciliationReport:
    legacy_counts: dict = field(default_factory=dict)
    expected_counts: dict = field(default_factory=dict)
    generic_counts: dict = field(default_factory=dict)
    issues: list[ReconciliationIssue] = field(default_factory=list)
    size_dimension_mismatches: list[str] = field(default_factory=list)

    @property
    def blocking_issues(self):
        return [issue for issue in self.issues if issue.level == BLOCKING]

    @property
    def warning_issues(self):
        return [issue for issue in self.issues if issue.level == WARNING]

    @property
    def can_apply(self):
        return not self.blocking_issues

    def add(self, level, code, message):
        self.issues.append(ReconciliationIssue(level, code, message))


class TileReconciliationBlocked(Exception):
    def __init__(self, report):
        self.report = report
        super().__init__("Tile catalog reconciliation has blocking issues.")


def _legacy_rows(spec):
    return list(spec.legacy_model.objects.all())


def _tile_product_queryset():
    """Legacy Tile ownership during coexistence: assigned Tile rows plus pre-backfill nulls."""
    return Product.objects.filter(Q(product_type__code="tile") | Q(product_type__isnull=True))


def _tile_variant_queryset():
    return ProductVariant.objects.filter(
        Q(product__product_type__code="tile") | Q(product__product_type__isnull=True)
    )


def _expected_option_rows(spec):
    return [
        {
            "code": row.slug,
            "name": row.name,
            "name_fa": row.name_fa,
            "sort_order": index,
            "is_active": True,
            "presentation_metadata": {},
            "legacy_id": row.pk,
        }
        for index, row in enumerate(_legacy_rows(spec), start=1)
    ]


def _legacy_counts():
    products = _tile_product_queryset()
    variants = _tile_variant_queryset()
    return {
        "products": products.count(),
        "variants": variants.count(),
        "brands": Brand.objects.count(),
        "designs": Design.objects.count(),
        "materials": Material.objects.count(),
        "usages": Usage.objects.count(),
        "colors": Color.objects.count(),
        "sizes": Size.objects.count(),
        "finishes": Finish.objects.count(),
    }


def _expected_counts():
    products = _tile_product_queryset()
    variants = _tile_variant_queryset()
    product_ids = products.values_list("pk", flat=True)
    product_value_counts = {
        "brand": products.filter(brand__isnull=False).count(),
        "design": Product.designs.through.objects.filter(product_id__in=product_ids).count(),
        "material": Product.materials.through.objects.filter(product_id__in=product_ids).count(),
        "usage": Product.usages.through.objects.filter(product_id__in=product_ids).count(),
    }
    variant_value_counts = {
        code: variants.filter(**{f"{code}__isnull": False}).count()
        for code in VARIANT_LEGACY_VALUES
    }
    option_counts = {
        spec.code: spec.legacy_model.objects.count() for spec in TILE_ATTRIBUTE_SPECS
    }
    return {
        "product_types": 1,
        "attribute_definitions": len(TILE_ATTRIBUTE_SPECS),
        "tile_bindings": len(TILE_ATTRIBUTE_SPECS),
        "option_restrictions": sum(option_counts.values()),
        "options": option_counts,
        "product_values": product_value_counts,
        "variant_values": variant_value_counts,
        "combination_signatures": variants.count(),
    }


def _generic_counts():
    tile = ProductType.objects.filter(code="tile").first()
    definitions = {
        definition.code: definition
        for definition in AttributeDefinition.objects.filter(code__in=SPEC_BY_CODE)
    }
    bindings = {}
    if tile:
        bindings = {
            binding.definition.code: binding
            for binding in tile.attribute_bindings.filter(definition__code__in=SPEC_BY_CODE)
            .select_related("definition")
        }
    return {
        "product_types": ProductType.objects.filter(code="tile").count(),
        "attribute_definitions": len(definitions),
        "tile_bindings": len(bindings),
        "option_restrictions": ProductTypeAttributeOption.objects.filter(
            binding__product_type=tile
        ).count() if tile else 0,
        # Count only legacy-mapped Tile identities. Shared definitions may validly
        # contain options owned by other ProductTypes.
        "options": {
            code: definition.options.filter(
                code__in=[row["code"] for row in _expected_option_rows(SPEC_BY_CODE[code])]
            ).count()
            for code, definition in definitions.items()
        },
        "product_values": {
            code: ProductAttributeValue.objects.filter(binding=binding).count()
            for code, binding in bindings.items() if code in PRODUCT_LEGACY_VALUES
        },
        "variant_values": {
            code: VariantAttributeValue.objects.filter(binding=binding).count()
            for code, binding in bindings.items() if code in VARIANT_LEGACY_VALUES
        },
        "combination_signatures": ProductVariant.objects.filter(
            product__product_type=tile,
            combination_signature__isnull=False
        ).count(),
    }


def _object_matches(instance, expected, fields):
    return all(getattr(instance, field) == expected[field] for field in fields)


def _inspect_schema(report, require_complete_generic):
    tile = ProductType.objects.filter(code="tile").first()
    if tile and not _object_matches(
        tile, TILE_PRODUCT_TYPE, ("name", "name_fa", "is_active", "sort_order")
    ):
        report.add(BLOCKING, "product_type_conflict", "ProductType code=tile conflicts with the approved Tile identity.")

    definitions = {}
    bindings = {}
    if tile:
        unexpected_binding_codes = set(
            tile.attribute_bindings.exclude(definition__code__in=SPEC_BY_CODE)
            .values_list("definition__code", flat=True)
        )
        for code in sorted(unexpected_binding_codes):
            report.add(
                BLOCKING,
                "unexpected_tile_binding",
                f"Tile ProductType has unexpected binding {code!r}.",
            )
    for spec in TILE_ATTRIBUTE_SPECS:
        definition = AttributeDefinition.objects.filter(code=spec.code).first()
        definitions[spec.code] = definition
        if definition:
            expected_definition = {
                "name": spec.name,
                "name_fa": spec.name_fa,
                "data_type": AttributeDefinition.DataType.OPTION,
                "unit": "",
                "validation_rules": {},
                "is_active": True,
            }
            if not _object_matches(definition, expected_definition, expected_definition):
                report.add(
                    BLOCKING,
                    "definition_conflict",
                    f"AttributeDefinition {spec.code!r} conflicts with the approved Tile schema.",
                )

            expected_options = {row["code"]: row for row in _expected_option_rows(spec)}
            existing_options = {
                option.code: option
                for option in definition.options.filter(code__in=expected_options)
            }
            for code, expected in expected_options.items():
                option = existing_options.get(code)
                if option and not _object_matches(
                    option,
                    expected,
                    ("name", "name_fa", "sort_order", "is_active", "presentation_metadata"),
                ):
                    report.add(
                        BLOCKING,
                        "option_identity_conflict",
                        f"Option {spec.code}:{code} does not match legacy row ID {expected['legacy_id']}.",
                    )

        if tile and definition:
            binding = ProductTypeAttribute.objects.filter(
                product_type=tile, definition=definition
            ).first()
            bindings[spec.code] = binding
            if binding and not _object_matches(binding, spec.binding_values, spec.binding_values):
                report.add(
                    BLOCKING,
                    "binding_conflict",
                    f"Tile binding {spec.code!r} conflicts with the approved schema configuration.",
                )
            if binding:
                expected_options = {
                    option.code: option
                    for option in definition.options.filter(
                        code__in=[row["code"] for row in _expected_option_rows(spec)]
                    )
                }
                memberships = {
                    membership.option.code: membership
                    for membership in binding.option_restrictions.select_related("option")
                }
                for code in sorted(set(memberships) - set(expected_options)):
                    report.add(
                        BLOCKING,
                        "unexpected_tile_option_membership",
                        f"Tile binding {spec.code!r} unexpectedly allows option {code!r}.",
                    )
                for expected in _expected_option_rows(spec):
                    option = expected_options.get(expected["code"])
                    membership = memberships.get(expected["code"])
                    if option is None:
                        continue
                    if membership is None:
                        report.add(
                            BLOCKING if require_complete_generic else INFO,
                            "tile_option_membership_missing",
                            f"Tile binding {spec.code!r} is missing option membership {expected['code']!r}.",
                        )
                    elif (
                        membership.option_id != option.pk
                        or not membership.is_active
                        or membership.sort_order != option.sort_order
                    ):
                        report.add(
                            BLOCKING,
                            "tile_option_membership_conflict",
                            f"Tile membership {spec.code}:{expected['code']} is inactive or incorrectly ordered.",
                        )
    return tile, definitions, bindings


def _inspect_required_dimensions_and_signatures(report):
    proposed_by_product = {}
    for variant in _tile_variant_queryset().select_related(
        "product", "color", "size", "finish"
    ).order_by("product_id", "id"):
        missing = [code for code in VARIANT_LEGACY_VALUES if getattr(variant, f"{code}_id") is None]
        for code in missing:
            report.add(
                BLOCKING,
                f"missing_{code}",
                f"Variant ID {variant.pk} SKU {variant.sku} is missing required {code}.",
            )
        if missing:
            continue
        pairs = [(code, getattr(variant, code).slug) for code in VARIANT_LEGACY_VALUES]
        try:
            signature = serialize_combination_signature(pairs)
        except ValidationError as exc:
            report.add(
                BLOCKING,
                "malformed_signature_source",
                f"Variant ID {variant.pk} SKU {variant.sku} has malformed stable codes: {exc}.",
            )
            continue
        prior = proposed_by_product.setdefault(variant.product_id, {}).get(signature)
        if prior:
            report.add(
                BLOCKING,
                "duplicate_combination",
                (
                    f"Product ID {variant.product_id} slug {variant.product.slug}: variants "
                    f"{prior.pk}/{prior.sku} and {variant.pk}/{variant.sku} both map to {signature}."
                ),
            )
        else:
            proposed_by_product[variant.product_id][signature] = variant
        if variant.combination_signature is not None and variant.combination_signature != signature:
            report.add(
                BLOCKING,
                "signature_conflict",
                (
                    f"Variant ID {variant.pk} SKU {variant.sku} has signature "
                    f"{variant.combination_signature!r}; expected {signature!r}."
                ),
            )


def _inspect_size_diagnostics(report):
    for variant in _tile_variant_queryset().select_related("size").filter(size__isnull=False).order_by("id"):
        mismatches = []
        if (
            variant.size.length_mm is not None
            and variant.length_mm is not None
            and variant.size.length_mm != variant.length_mm
        ):
            mismatches.append(f"length size={variant.size.length_mm} variant={variant.length_mm}")
        if (
            variant.size.width_mm is not None
            and variant.width_mm is not None
            and variant.size.width_mm != variant.width_mm
        ):
            mismatches.append(f"width size={variant.size.width_mm} variant={variant.width_mm}")
        if mismatches:
            detail = f"Variant ID {variant.pk} SKU {variant.sku}: " + ", ".join(mismatches)
            report.size_dimension_mismatches.append(detail)
            report.add(WARNING, "size_dimension_mismatch", detail)


def _inspect_existing_values(report, tile, bindings, require_complete_generic):
    # Non-Tile products are legitimate. Only a row still carrying authoritative
    # legacy Tile taxonomy/dimensions is a ProductType ownership conflict.
    for product in Product.objects.exclude(product_type__isnull=True).exclude(
        product_type__code="tile"
    ).select_related("product_type"):
        has_tile_shape = (
            product.brand_id is not None
            or product.designs.exists()
            or product.materials.exists()
            or product.usages.exists()
            or product.variants.filter(
                Q(color__isnull=False) | Q(size__isnull=False) | Q(finish__isnull=False)
            ).exists()
        )
        if has_tile_shape:
            report.add(
                BLOCKING,
                "product_type_conflict",
                f"Product ID {product.pk} SKU {product.sku} has legacy Tile data but ProductType {product.product_type.code!r}.",
            )

    for code, value_getter in PRODUCT_LEGACY_VALUES.items():
        binding = bindings.get(code)
        if not binding:
            continue
        for product in _tile_product_queryset().select_related("brand").prefetch_related(
            "designs", "materials", "usages"
        ):
            expected_codes = {row.slug for row in value_getter(product)}
            existing = list(
                product.attribute_values.filter(binding=binding).select_related("option_value__definition")
            )
            existing_codes = set()
            invalid_carrier = False
            for value in existing:
                if value.option_value_id is None or value.option_value.definition_id != binding.definition_id:
                    invalid_carrier = True
                else:
                    existing_codes.add(value.option_value.code)
            if invalid_carrier or not existing_codes.issubset(expected_codes):
                report.add(
                    BLOCKING,
                    "product_value_conflict",
                    f"Product ID {product.pk} SKU {product.sku} has conflicting generic {code} values.",
                )
            elif require_complete_generic and existing_codes != expected_codes:
                report.add(
                    BLOCKING,
                    "product_value_missing",
                    f"Product ID {product.pk} SKU {product.sku} is missing generic {code} parity.",
                )

    for code, value_getter in VARIANT_LEGACY_VALUES.items():
        binding = bindings.get(code)
        if not binding:
            continue
        for variant in _tile_variant_queryset().select_related(code):
            legacy = value_getter(variant)
            expected_code = legacy.slug if legacy else None
            existing = list(
                variant.attribute_values.filter(binding=binding).select_related("option_value__definition")
            )
            exact = (
                len(existing) == 1
                and existing[0].option_value_id is not None
                and existing[0].option_value.definition_id == binding.definition_id
                and existing[0].option_value.code == expected_code
            )
            if existing and not exact:
                report.add(
                    BLOCKING,
                    "variant_value_conflict",
                    f"Variant ID {variant.pk} SKU {variant.sku} has conflicting generic {code} value.",
                )
            elif require_complete_generic and expected_code is not None and not exact:
                report.add(
                    BLOCKING,
                    "variant_value_missing",
                    f"Variant ID {variant.pk} SKU {variant.sku} is missing generic {code} parity.",
                )

    if require_complete_generic and tile:
        for product in _tile_product_queryset().exclude(product_type=tile):
            report.add(
                BLOCKING,
                "product_type_missing",
                f"Product ID {product.pk} SKU {product.sku} is not assigned to ProductType tile.",
            )
        for variant in ProductVariant.objects.filter(
            product__product_type=tile, combination_signature__isnull=True
        ):
            report.add(
                BLOCKING,
                "signature_missing",
                f"Variant ID {variant.pk} SKU {variant.sku} has no combination signature.",
            )


def inspect_tile_catalog(*, require_complete_generic=False):
    report = TileReconciliationReport(
        legacy_counts=_legacy_counts(),
        expected_counts=_expected_counts(),
    )
    tile, _, bindings = _inspect_schema(report, require_complete_generic)
    _inspect_required_dimensions_and_signatures(report)
    _inspect_size_diagnostics(report)
    _inspect_existing_values(report, tile, bindings, require_complete_generic)
    report.generic_counts = _generic_counts()
    report.add(
        INFO,
        "preflight_complete",
        f"Inspected {report.legacy_counts['products']} products and {report.legacy_counts['variants']} variants.",
    )
    return report


def capture_preservation_baseline():
    Order = apps.get_model("orders", "Order")
    OrderItem = apps.get_model("orders", "OrderItem")
    return {
        "products": list(Product.objects.order_by("id").values_list("id", "sku", "primary_category_id")),
        "variants": list(
            ProductVariant.objects.order_by("id").values_list(
                "id", "sku", "stock", "low_stock_threshold", "is_active", "sort_order"
            )
        ),
        "variant_prices": list(
            VariantPrice.objects.order_by("id").values_list(
                "id", "variant_id", "currency", "amount", "compare_at_amount"
            )
        ),
        "product_images": ProductImage.objects.count(),
        "variant_images": VariantImage.objects.count(),
        "categories": list(Category.objects.order_by("id").values_list("id", "parent_id")),
        "product_categories": list(
            Product.categories.through.objects.order_by("product_id", "category_id").values_list(
                "product_id", "category_id"
            )
        ),
        "orders": list(Order.objects.order_by("id").values_list("id", flat=True)),
        "order_items": list(OrderItem.objects.order_by("id").values_list("id", flat=True)),
    }


def _create_validated(model, **values):
    instance = model(**values)
    instance.full_clean()
    instance.save()
    return instance


def _apply_schema():
    tile = ProductType.objects.filter(code="tile").first()
    if tile is None:
        tile = _create_validated(ProductType, **TILE_PRODUCT_TYPE)

    definitions = {}
    bindings = {}
    options = {}
    for spec in TILE_ATTRIBUTE_SPECS:
        definition = AttributeDefinition.objects.filter(code=spec.code).first()
        if definition is None:
            definition = _create_validated(
                AttributeDefinition,
                code=spec.code,
                name=spec.name,
                name_fa=spec.name_fa,
                data_type=AttributeDefinition.DataType.OPTION,
                unit="",
                validation_rules={},
                is_active=True,
            )
        definitions[spec.code] = definition

        binding = ProductTypeAttribute.objects.filter(
            product_type=tile, definition=definition
        ).first()
        if binding is None:
            binding = _create_validated(
                ProductTypeAttribute,
                product_type=tile,
                definition=definition,
                **spec.binding_values,
            )
        bindings[spec.code] = binding

        options[spec.code] = {}
        for expected in _expected_option_rows(spec):
            option = AttributeOption.objects.filter(
                definition=definition, code=expected["code"]
            ).first()
            if option is None:
                option = _create_validated(
                    AttributeOption,
                    definition=definition,
                    code=expected["code"],
                    name=expected["name"],
                    name_fa=expected["name_fa"],
                    sort_order=expected["sort_order"],
                    is_active=True,
                    presentation_metadata={},
                )
            options[spec.code][expected["legacy_id"]] = option
            membership = ProductTypeAttributeOption.objects.filter(
                binding=binding, option=option
            ).first()
            if membership is None:
                _create_validated(
                    ProductTypeAttributeOption,
                    binding=binding,
                    option=option,
                    sort_order=option.sort_order,
                    is_active=True,
                )
    return tile, bindings, options


def _apply_product_values(tile, bindings, options):
    Product.objects.filter(product_type__isnull=True).update(product_type=tile)
    for product in Product.objects.filter(product_type=tile).select_related("brand").prefetch_related(
        "designs", "materials", "usages"
    ):
        for code, value_getter in PRODUCT_LEGACY_VALUES.items():
            binding = bindings[code]
            existing_option_ids = set(
                product.attribute_values.filter(binding=binding).values_list("option_value_id", flat=True)
            )
            for legacy_value in value_getter(product):
                option = options[code][legacy_value.pk]
                if option.pk not in existing_option_ids:
                    _create_validated(
                        ProductAttributeValue,
                        product=product,
                        binding=binding,
                        option_value=option,
                    )


def _apply_variant_values(bindings, options):
    tile = bindings["color"].product_type
    for variant in ProductVariant.objects.filter(product__product_type=tile).select_related("color", "size", "finish"):
        for code, value_getter in VARIANT_LEGACY_VALUES.items():
            binding = bindings[code]
            legacy_value = value_getter(variant)
            if not variant.attribute_values.filter(binding=binding).exists():
                _create_validated(
                    VariantAttributeValue,
                    variant=variant,
                    binding=binding,
                    option_value=options[code][legacy_value.pk],
                )


def _apply_signatures():
    for variant in ProductVariant.objects.filter(product__product_type__code="tile").select_related("product__product_type").order_by("id"):
        expected = calculate_variant_combination_signature(variant)
        if variant.combination_signature is None:
            variant.combination_signature = expected
            variant.full_clean()
            variant.save(update_fields=["combination_signature"])


@transaction.atomic
def apply_tile_catalog(*, failure_hook: Callable | None = None):
    # Lock authoritative rows and re-run the complete preflight inside the write transaction.
    list(Product.objects.select_for_update().values_list("pk", flat=True))
    list(ProductVariant.objects.select_for_update().values_list("pk", flat=True))
    before = capture_preservation_baseline()
    preflight = inspect_tile_catalog()
    if preflight.blocking_issues:
        raise TileReconciliationBlocked(preflight)

    tile, bindings, options = _apply_schema()
    _apply_product_values(tile, bindings, options)
    if failure_hook:
        failure_hook()
    _apply_variant_values(bindings, options)
    _apply_signatures()

    after = capture_preservation_baseline()
    if before != after:
        report = inspect_tile_catalog()
        report.add(
            BLOCKING,
            "preservation_failure",
            "Commerce/catalog identity baseline changed during F1B.2 apply.",
        )
        raise TileReconciliationBlocked(report)

    postflight = inspect_tile_catalog(require_complete_generic=True)
    if postflight.blocking_issues:
        raise TileReconciliationBlocked(postflight)
    return postflight
