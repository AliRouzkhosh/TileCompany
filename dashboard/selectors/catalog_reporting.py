"""Catalog reporting projections with explicit historical/current authorities.

Sold-line projections consume only the immutable OrderItem snapshot.  Current
inventory projections consume only current ProductVariant and generic catalog
rows.  Keeping these paths separate prevents surviving catalog foreign keys
from rewriting history in analytics.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from products.catalog_read import read_product_details
from products.models import ProductVariant
from products.purchase_snapshot import (
    PurchaseSnapshotIntegrityError,
    PurchasedAttributeDTO,
    parse_purchase_snapshot,
)


LEGACY_UNKNOWN_UNIT = "legacy_unknown"


@dataclass(frozen=True, slots=True)
class ReportingAttributeDTO:
    scope: str
    definition_code: str
    label_default: str
    values: tuple[str, ...]
    stable_values: tuple[str, ...]
    sort_order: int
    is_dimension: bool


@dataclass(frozen=True, slots=True)
class PurchasedReportingLineDTO:
    item_id: int
    order_id: int
    currency: str
    product_id: int | None
    product_type_code: str
    product_name: str
    product_sku: str
    variant_id: int | None
    variant_sku: str
    selling_unit_code: str
    quantity: int
    unit_price: Decimal
    revenue: Decimal
    product_attributes: tuple[ReportingAttributeDTO, ...]
    variant_attributes: tuple[ReportingAttributeDTO, ...]
    operational: tuple[tuple[str, str], ...]
    snapshot_source: str
    snapshot_issue: str


@dataclass(frozen=True, slots=True)
class CurrentInventoryLineDTO:
    product_id: int
    product_type_code: str
    product_name: str
    product_sku: str
    variant_id: int
    variant_sku: str
    selling_unit_code: str
    stock: int
    low_stock_threshold: int
    inventory_state: str
    product_attributes: tuple[ReportingAttributeDTO, ...]
    variant_attributes: tuple[ReportingAttributeDTO, ...]


def _reporting_attribute(attribute: PurchasedAttributeDTO) -> ReportingAttributeDTO:
    return ReportingAttributeDTO(
        scope=attribute.scope,
        definition_code=attribute.definition_code,
        label_default=attribute.label_default,
        values=tuple(value.display_default for value in attribute.values),
        stable_values=tuple(
            str(value.option_code if value.option_code is not None else value.raw_value)
            for value in attribute.values
        ),
        sort_order=attribute.sort_order,
        is_dimension=attribute.is_dimension,
    )


def _flatten_operational(value, prefix=""):
    rows = []
    if isinstance(value, Mapping):
        for key in sorted(value):
            name = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_flatten_operational(value[key], name))
    elif value not in (None, "", [], {}):
        rows.append((prefix, str(value)))
    return rows


def project_purchased_lines(items: Iterable) -> tuple[PurchasedReportingLineDTO, ...]:
    """Project sold rows from frozen JSON and OrderItem commerce facts only."""

    result = []
    for item in items:
        snapshot = item.variant_snapshot or {}
        try:
            parsed = parse_purchase_snapshot(snapshot)
        except PurchaseSnapshotIntegrityError as error:
            # Never heal malformed history from current catalog rows.  Retain the
            # immutable OrderItem facts and expose an explicit diagnostic.
            product = {}
            variant = {}
            product_attributes = ()
            variant_attributes = ()
            operational = ()
            source = "invalid"
            issue = str(error)
        else:
            product = parsed.product
            variant = parsed.variant
            product_attributes = tuple(
                _reporting_attribute(attribute)
                for attribute in parsed.product_attributes
            )
            variant_attributes = tuple(
                _reporting_attribute(attribute)
                for attribute in parsed.variant_attributes
            )
            operational = tuple(_flatten_operational(dict(parsed.operational)))
            source = parsed.source
            issue = ""

        product_type = product.get("product_type", {})
        selling_unit = variant.get("selling_unit", {})
        # Legacy snapshots contain no frozen selling-unit proof.  A surviving
        # Variant FK is deliberately ignored.
        unit_code = (
            str(selling_unit.get("code"))
            if source == "v2" and selling_unit.get("code")
            else LEGACY_UNKNOWN_UNIT
        )
        product_name = str(product.get("name_default") or item.product_name or "Historical product")
        variant_sku = str(variant.get("sku") or snapshot.get("sku") or "Historical SKU")
        quantity = int(item.quantity)
        unit_price = item.unit_price
        result.append(PurchasedReportingLineDTO(
            item_id=item.pk,
            order_id=item.order_id,
            currency=item.order.currency,
            product_id=product.get("id"),
            product_type_code=str(product_type.get("code") or ""),
            product_name=product_name,
            product_sku=str(product.get("sku") or ""),
            variant_id=variant.get("id"),
            variant_sku=variant_sku,
            selling_unit_code=unit_code,
            quantity=quantity,
            unit_price=unit_price,
            revenue=unit_price * quantity,
            product_attributes=product_attributes,
            variant_attributes=variant_attributes,
            operational=operational,
            snapshot_source=source,
            snapshot_issue=issue,
        ))
    return tuple(result)


def _current_attribute_groups(groups):
    return tuple(
        ReportingAttributeDTO(
            scope=group.scope,
            definition_code=group.definition_code,
            label_default=group.label,
            values=tuple(value.display_value for value in group.values),
            stable_values=tuple(
                str(value.value_code if value.value_code is not None else value.raw_value)
                for value in group.values
            ),
            sort_order=group.sort_order,
            is_dimension=group.is_variant_dimension,
        )
        for group in groups
        if group.values
    )


def _current_variant_attributes(values):
    grouped = defaultdict(list)
    metadata = {}
    for value in values:
        grouped[value.definition_code].append(value)
        metadata.setdefault(value.definition_code, value)
    result = []
    for code, rows in grouped.items():
        first = metadata[code]
        result.append(ReportingAttributeDTO(
            scope=first.scope,
            definition_code=code,
            label_default=first.label,
            values=tuple(row.display_value for row in rows),
            stable_values=tuple(
                str(row.value_code if row.value_code is not None else row.raw_value)
                for row in rows
            ),
            sort_order=first.sort_order,
            is_dimension=first.is_variant_dimension,
        ))
    return tuple(sorted(result, key=lambda row: (row.sort_order, row.definition_code)))


def project_current_inventory(variants=None) -> tuple[CurrentInventoryLineDTO, ...]:
    """Project current stock from live Variant and generic attribute truth."""

    if variants is None:
        variants = ProductVariant.objects.filter(
            is_active=True, product__is_active=True,
        ).select_related("product", "product__product_type").order_by("product_id", "sort_order", "id")
    variants = list(variants)
    if not variants:
        return ()
    products = list({variant.product_id: variant.product for variant in variants}.values())
    details = read_product_details(products, language="en")
    variant_dtos = {
        row.id: row
        for detail in details.values()
        for row in detail.variants
    }
    result = []
    for variant in variants:
        detail = details[variant.product_id]
        variant_dto = variant_dtos[variant.pk]
        if variant.stock == 0:
            state = "Out of Stock"
        elif variant.stock <= variant.low_stock_threshold:
            state = "Low Stock"
        else:
            state = "Healthy"
        result.append(CurrentInventoryLineDTO(
            product_id=variant.product_id,
            product_type_code=detail.product_type_code,
            product_name=variant.product.name,
            product_sku=variant.product.sku,
            variant_id=variant.pk,
            variant_sku=variant.sku,
            selling_unit_code=variant.price_unit,
            stock=variant.stock,
            low_stock_threshold=variant.low_stock_threshold,
            inventory_state=state,
            product_attributes=_current_attribute_groups(
                detail.product_attributes.attributes
            ),
            variant_attributes=_current_variant_attributes(
                variant_dto.attribute_values
            ),
        ))
    return tuple(result)


def quantity_by_selling_unit(lines: Iterable) -> dict[str, int]:
    totals = defaultdict(int)
    for line in lines:
        totals[line.selling_unit_code] += int(line.quantity)
    return dict(sorted(totals.items()))
