"""Pure, batch publication-readiness evaluation for catalog Products/Variants."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from .catalog_domain import (
    VALUE_FIELD_BY_DATA_TYPE,
    VALUE_FIELDS,
    serialize_combination_signature,
    validate_scalar_against_rules,
)
from .catalog_read import CatalogReadIntegrityError, read_product_details
from .currency import Currency
from .models import (
    Product,
    ProductAttributeValue,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    VariantAttributeValue,
    VariantPrice,
)
from .purchase_snapshot import PurchaseSnapshotIntegrityError, prepare_purchase_snapshots
from .storefront_currency_policy import required_storefront_currencies


class PublicationSeverity(StrEnum):
    BLOCK = "BLOCK"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass(frozen=True, slots=True)
class PublicationIssueDTO:
    code: str
    severity: PublicationSeverity
    message: str
    path: str = ""
    related_identity: str = ""


class _IssuePartitions:
    issues: tuple[PublicationIssueDTO, ...]

    @property
    def blockers(self):
        return tuple(issue for issue in self.issues if issue.severity == PublicationSeverity.BLOCK)

    @property
    def warnings(self):
        return tuple(issue for issue in self.issues if issue.severity == PublicationSeverity.WARNING)

    @property
    def info(self):
        return tuple(issue for issue in self.issues if issue.severity == PublicationSeverity.INFO)

    @property
    def ready(self):
        return not self.blockers


@dataclass(frozen=True, slots=True)
class VariantPublicationReadinessDTO(_IssuePartitions):
    variant_id: int
    sku: str
    is_active: bool
    issues: tuple[PublicationIssueDTO, ...]
    required_currencies: tuple[str, ...]
    available_currencies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProductPublicationReadinessDTO(_IssuePartitions):
    product_id: int
    sku: str
    is_active: bool
    issues: tuple[PublicationIssueDTO, ...]
    ready_variant_count: int
    active_ready_variant_count: int
    variant_results: tuple[VariantPublicationReadinessDTO, ...]


@dataclass(frozen=True, slots=True)
class PublicationReadinessBatchDTO:
    product_results: tuple[ProductPublicationReadinessDTO, ...]

    @property
    def variant_results(self):
        return tuple(result for product in self.product_results for result in product.variant_results)


def _issue(code, severity, message, *, path="", identity=""):
    return PublicationIssueDTO(code, severity, message, path, identity)


def _validation_text(error):
    if hasattr(error, "message_dict"):
        return "; ".join(
            f"{field}: {', '.join(str(value) for value in values)}"
            for field, values in error.message_dict.items()
        )
    return "; ".join(str(value) for value in error.messages)


def _allowed_option_ids(binding, restrictions):
    rows = restrictions.get(binding.pk, ())
    if rows:
        return {row.option_id for row in rows if row.is_active and row.option.is_active}
    return None


def _validate_value(owner_label, binding, value, allowed_ids):
    issues = []
    definition = binding.definition
    identity = f"{binding.scope}.{definition.code}"
    populated = [field for field in VALUE_FIELDS if getattr(value, field) is not None]
    expected = VALUE_FIELD_BY_DATA_TYPE.get(definition.data_type)
    if populated != [expected]:
        return [_issue(
            "invalid_typed_attribute_value", PublicationSeverity.BLOCK,
            _("%(owner)s attribute %(attribute)s does not use its configured %(type)s value type.") % {"owner": owner_label, "attribute": definition.name, "type": definition.data_type},
            path=identity, identity=definition.code,
        )]
    if definition.data_type == "option":
        option = value.option_value
        if option.definition_id != definition.pk:
            issues.append(_issue(
                "option_definition_mismatch", PublicationSeverity.BLOCK,
                _("Option '%(option)s' does not belong to %(attribute)s.") % {"option": option.name, "attribute": definition.name},
                path=identity, identity=option.code,
            ))
        elif not option.is_active:
            issues.append(_issue(
                "retired_option", PublicationSeverity.BLOCK,
                _("Option '%(option)s' is retired for %(attribute)s.") % {"option": option.name, "attribute": definition.name},
                path=identity, identity=option.code,
            ))
        elif allowed_ids is not None and option.pk not in allowed_ids:
            issues.append(_issue(
                "disallowed_option", PublicationSeverity.BLOCK,
                _("Option '%(option)s' is no longer allowed for %(attribute)s.") % {"option": option.name, "attribute": definition.name},
                path=identity, identity=option.code,
            ))
    else:
        try:
            validate_scalar_against_rules(definition, getattr(value, expected))
        except (ValidationError, TypeError, ValueError) as error:
            issues.append(_issue(
                "attribute_validation_failed", PublicationSeverity.BLOCK,
                _("%(owner)s attribute %(attribute)s is invalid: %(error)s") % {"owner": owner_label, "attribute": definition.name, "error": _validation_text(error) if isinstance(error, ValidationError) else error},
                path=identity, identity=definition.code,
            ))
    return issues


def _validate_owner_values(owner_label, bindings, values, restrictions, *, required_code):
    issues = []
    active = {binding.pk: binding for binding in bindings if binding.is_active}
    rows_by_binding = defaultdict(list)
    for value in values:
        binding = active.get(value.binding_id)
        if binding is None:
            issues.append(_issue(
                "value_outside_active_schema", PublicationSeverity.BLOCK,
                _("%(owner)s contains a value outside its active schema.") % {"owner": owner_label},
                path="attributes",
            ))
            continue
        rows_by_binding[binding.pk].append(value)
    for binding in active.values():
        rows = rows_by_binding.get(binding.pk, [])
        if binding.is_required and not rows:
            issues.append(_issue(
                required_code, PublicationSeverity.BLOCK,
                _("Missing required %(scope)s attribute: %(attribute)s.") % {"scope": binding.scope, "attribute": binding.definition.name},
                path=f"{binding.scope}.{binding.definition.code}", identity=binding.definition.code,
            ))
        if binding.cardinality == ProductTypeAttribute.Cardinality.SINGLE and len(rows) > 1:
            issues.append(_issue(
                "attribute_cardinality_invalid", PublicationSeverity.BLOCK,
                _("Attribute %(attribute)s must have exactly one value.") % {"attribute": binding.definition.name},
                path=f"{binding.scope}.{binding.definition.code}", identity=binding.definition.code,
            ))
        allowed = _allowed_option_ids(binding, restrictions)
        for value in rows:
            issues.extend(_validate_value(owner_label, binding, value, allowed))
    return issues, rows_by_binding


def _validate_price(price):
    if price.currency not in Currency.values:
        return "Unsupported currency."
    if price.amount is None or price.amount < 0:
        return "Price amount cannot be negative."
    if price.compare_at_amount is not None and price.compare_at_amount <= price.amount:
        return "Compare-at price must be greater than the selling price."
    if price.currency == Currency.TOMAN and any(
        value is not None and value != value.to_integral_value()
        for value in (price.amount, price.compare_at_amount)
    ):
        return "Toman prices must be whole Toman amounts."
    return ""


def evaluate_publication_readiness(products: Iterable[Product] | None = None, *, required_currencies=None):
    """Evaluate one/many Products with a bounded, side-effect-free query plan."""

    required = required_storefront_currencies(required_currencies)
    product_ids = None if products is None else [item.pk for item in products]
    product_qs = Product.objects.select_related("product_type", "primary_category").prefetch_related("categories", "materials").order_by("id")
    if product_ids is not None:
        product_qs = product_qs.filter(pk__in=product_ids)
    product_list = list(product_qs)
    if not product_list:
        return PublicationReadinessBatchDTO(())
    products_by_id = {product.pk: product for product in product_list}
    type_ids = {product.product_type_id for product in product_list if product.product_type_id}
    bindings = list(
        ProductTypeAttribute.objects.filter(product_type_id__in=type_ids)
        .select_related("definition", "product_type").order_by("product_type_id", "scope", "sort_order", "id")
    )
    bindings_by_type_scope = defaultdict(list)
    for binding in bindings:
        bindings_by_type_scope[(binding.product_type_id, binding.scope)].append(binding)
    restriction_rows = list(
        ProductTypeAttributeOption.objects.filter(binding__product_type_id__in=type_ids)
        .select_related("option").order_by("binding_id", "sort_order", "id")
    )
    restrictions = defaultdict(list)
    for row in restriction_rows:
        restrictions[row.binding_id].append(row)
    product_values = defaultdict(list)
    for value in ProductAttributeValue.objects.filter(product_id__in=products_by_id).select_related("binding__definition", "option_value").order_by("product_id", "binding_id", "sort_order", "id"):
        product_values[value.product_id].append(value)
    variants = list(
        ProductVariant.objects.filter(product_id__in=products_by_id)
        .select_related("product__product_type").order_by("product_id", "sort_order", "id")
    )
    variants_by_product = defaultdict(list)
    for variant in variants:
        variant.product = products_by_id[variant.product_id]
        variants_by_product[variant.product_id].append(variant)
    variant_values = defaultdict(list)
    for value in VariantAttributeValue.objects.filter(variant_id__in=[variant.pk for variant in variants]).select_related("binding__definition", "option_value").order_by("variant_id", "binding_id", "id"):
        variant_values[value.variant_id].append(value)
    prices = defaultdict(list)
    for price in VariantPrice.objects.filter(variant_id__in=[variant.pk for variant in variants]).order_by("variant_id", "currency", "id"):
        prices[price.variant_id].append(price)

    product_issues = defaultdict(list)
    variant_issues = defaultdict(list)
    variant_rows_by_binding = {}
    for product in product_list:
        if not product.product_type_id:
            product_issues[product.pk].append(_issue("missing_product_type", PublicationSeverity.BLOCK, _("ProductType is required."), path="product_type"))
            continue
        if not product.product_type.is_active:
            product_issues[product.pk].append(_issue("inactive_product_type", PublicationSeverity.BLOCK, _("ProductType %(type)s is inactive.") % {"type": product.product_type.name}, path="product_type", identity=product.product_type.code))
        pbindings = bindings_by_type_scope[(product.product_type_id, ProductTypeAttribute.Scope.PRODUCT)]
        for binding in (*pbindings, *bindings_by_type_scope[(product.product_type_id, ProductTypeAttribute.Scope.VARIANT)]):
            if binding.is_active and not binding.definition.is_active:
                product_issues[product.pk].append(_issue("inactive_attribute_definition", PublicationSeverity.BLOCK, _("Active schema attribute %(attribute)s is inactive.") % {"attribute": binding.definition.name}, path=f"schema.{binding.definition.code}", identity=binding.definition.code))
        issues, ignored_product_rows = _validate_owner_values("Product", pbindings, product_values[product.pk], restrictions, required_code="missing_required_product_attribute")
        product_issues[product.pk].extend(issues)
        if product.primary_category_id and product.primary_category_id not in {category.pk for category in product.categories.all()}:
            product_issues[product.pk].append(_issue("primary_category_not_assigned", PublicationSeverity.BLOCK, _("Primary category must also belong to the Product's Categories."), path="primary_category"))

    signature_counts = defaultdict(int)
    for variant in variants:
        product = products_by_id[variant.product_id]
        if not variant.sku.strip():
            variant_issues[variant.pk].append(_issue("missing_variant_sku", PublicationSeverity.BLOCK, _("Variant SKU is required."), path="sku"))
        if variant.price_unit not in ProductVariant.PriceUnit.values:
            variant_issues[variant.pk].append(_issue("invalid_selling_unit", PublicationSeverity.BLOCK, _("Select a valid selling unit."), path="price_unit", identity=str(variant.price_unit)))
        if variant.stock < 0:
            variant_issues[variant.pk].append(_issue("negative_stock", PublicationSeverity.BLOCK, _("Stock cannot be negative."), path="stock"))
        elif variant.stock == 0:
            variant_issues[variant.pk].append(_issue("zero_stock", PublicationSeverity.WARNING, _("Variant has zero stock and will appear out of stock."), path="stock"))
        if variant.low_stock_threshold < 0:
            variant_issues[variant.pk].append(_issue("negative_low_stock_threshold", PublicationSeverity.BLOCK, _("Low-stock threshold cannot be negative."), path="low_stock_threshold"))
        if not product.product_type_id:
            variant_issues[variant.pk].append(_issue("missing_product_type", PublicationSeverity.BLOCK, _("The Variant's Product requires a ProductType."), path="product.product_type"))
            continue
        if not product.product_type.is_active:
            variant_issues[variant.pk].append(_issue("inactive_product_type", PublicationSeverity.BLOCK, _("ProductType %(type)s is inactive.") % {"type": product.product_type.name}, path="product.product_type", identity=product.product_type.code))
        vbindings = bindings_by_type_scope[(product.product_type_id, ProductTypeAttribute.Scope.VARIANT)]
        issues, rows_by_binding = _validate_owner_values("Variant", vbindings, variant_values[variant.pk], restrictions, required_code="missing_required_variant_attribute")
        variant_issues[variant.pk].extend(issues)
        variant_rows_by_binding[variant.pk] = rows_by_binding
        dimensions = [binding for binding in vbindings if binding.is_active and binding.is_variant_dimension]
        pairs = []
        complete = True
        for binding in dimensions:
            rows = rows_by_binding.get(binding.pk, [])
            if len(rows) != 1 or not rows[0].option_value_id:
                complete = False
                variant_issues[variant.pk].append(_issue("missing_required_variant_dimension", PublicationSeverity.BLOCK, _("Missing required Variant dimension: %(attribute)s.") % {"attribute": binding.definition.name}, path=f"variant.{binding.definition.code}", identity=binding.definition.code))
            else:
                pairs.append((binding.definition.code, rows[0].option_value.code))
        expected = serialize_combination_signature(pairs) if complete else None
        if complete and variant.combination_signature != expected:
            variant_issues[variant.pk].append(_issue("invalid_combination_signature", PublicationSeverity.BLOCK, _("Combination signature is invalid; expected %(signature)s.") % {"signature": expected}, path="combination_signature"))
        if variant.combination_signature is not None:
            signature_counts[(variant.product_id, variant.combination_signature)] += 1
        price_by_currency = {price.currency: price for price in prices[variant.pk]}
        for currency in required:
            price = price_by_currency.get(currency)
            if price is None:
                variant_issues[variant.pk].append(_issue("missing_currency_price", PublicationSeverity.BLOCK, _("Missing required price: %(currency)s.") % {"currency": currency}, path=f"prices.{currency}", identity=currency))
            else:
                error = _validate_price(price)
                if error:
                    variant_issues[variant.pk].append(_issue("invalid_currency_price", PublicationSeverity.BLOCK, _("Invalid %(currency)s price: %(error)s") % {"currency": currency, "error": error}, path=f"prices.{currency}", identity=currency))

    for variant in variants:
        if variant.combination_signature is not None and signature_counts[(variant.product_id, variant.combination_signature)] > 1:
            variant_issues[variant.pk].append(_issue("duplicate_combination", PublicationSeverity.BLOCK, _("Another Variant uses this complete attribute combination."), path="combination_signature"))

    # Exercise the actual generic read and snapshot consumers once in batches.
    readable_products = [product for product in product_list if product.product_type_id]
    try:
        read_product_details(readable_products, language="en")
    except CatalogReadIntegrityError as error:
        # A batch failure must not make unrelated Products look invalid. The
        # normal path stays batched; this diagnostic isolation runs only for
        # already-corrupt data.
        for product in readable_products:
            try:
                read_product_details([product], language="en")
            except CatalogReadIntegrityError as product_error:
                product_issues[product.pk].append(_issue("generic_read_integrity_failed", PublicationSeverity.BLOCK, _("Generic catalog presentation is not readable: %(error)s") % {"error": product_error}, path="catalog_read"))
    for variant in variants:
        product_blockers = [
            issue for issue in product_issues[variant.product_id]
            if issue.severity == PublicationSeverity.BLOCK
        ]
        if product_blockers:
            variant_issues[variant.pk].append(_issue("product_catalog_not_ready", PublicationSeverity.BLOCK, _("The Variant's Product data is not publication-ready."), path="product"))
    snapshot_candidates = [
        variant for variant in variants
        if not any(issue.severity == PublicationSeverity.BLOCK for issue in variant_issues[variant.pk])
    ]
    if snapshot_candidates:
        try:
            prepare_purchase_snapshots(snapshot_candidates)
        except PurchaseSnapshotIntegrityError as error:
            # As above, isolate an exceptional candidate without penalizing
            # otherwise-ready siblings. This path is diagnostic, not normal.
            for variant in snapshot_candidates:
                try:
                    prepare_purchase_snapshots([variant])
                except PurchaseSnapshotIntegrityError as variant_error:
                    variant_issues[variant.pk].append(_issue("snapshot_readiness_failed", PublicationSeverity.BLOCK, _("Purchase snapshot cannot be prepared: %(error)s") % {"error": variant_error}, path="purchase_snapshot"))

    variant_results_by_product = defaultdict(list)
    for variant in variants:
        available = tuple(sorted({price.currency for price in prices[variant.pk]}))
        result = VariantPublicationReadinessDTO(
            variant_id=variant.pk, sku=variant.sku, is_active=variant.is_active,
            issues=tuple(variant_issues[variant.pk]), required_currencies=required,
            available_currencies=available,
        )
        variant_results_by_product[variant.product_id].append(result)

    product_results = []
    for product in product_list:
        variant_results = tuple(variant_results_by_product[product.pk])
        ready_count = sum(result.ready for result in variant_results)
        active_ready_count = sum(result.ready and result.is_active for result in variant_results)
        issues = list(product_issues[product.pk])
        if not active_ready_count:
            issues.append(_issue("no_active_publication_ready_variant", PublicationSeverity.BLOCK, _("Activate at least one publication-ready Variant before publishing the Product."), path="variants"))
        incomplete_drafts = sum(not result.ready and not result.is_active for result in variant_results)
        if incomplete_drafts:
            issues.append(_issue("incomplete_draft_variants", PublicationSeverity.INFO, _("%(count)s inactive draft Variant(s) are incomplete; they do not prevent publishing with another active ready Variant.") % {"count": incomplete_drafts}, path="variants"))
        product_results.append(ProductPublicationReadinessDTO(
            product_id=product.pk, sku=product.sku, is_active=product.is_active,
            issues=tuple(issues), ready_variant_count=ready_count,
            active_ready_variant_count=active_ready_count,
            variant_results=variant_results,
        ))
    return PublicationReadinessBatchDTO(tuple(product_results))


def evaluate_product_publication_readiness(product, *, required_currencies=None):
    return evaluate_publication_readiness([product], required_currencies=required_currencies).product_results[0]


def evaluate_variant_publication_readiness(variant, *, required_currencies=None):
    product = evaluate_product_publication_readiness(variant.product, required_currencies=required_currencies)
    return next(result for result in product.variant_results if result.variant_id == variant.pk)
