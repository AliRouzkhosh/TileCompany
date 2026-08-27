"""Generic, read-only commerce presentation contracts.

ProductVariant remains the owner of selling unit, stock, and commerce identity.
This module only prepares localized immutable DTOs for public presentation.
"""

from __future__ import annotations

import logging
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from django.utils.translation import gettext as _, ngettext, override

from .catalog_presentation import (
    LEGACY_FALLBACK_SOURCE,
    compose_product_detail_presentation,
)
from .catalog_read import CatalogReadIntegrityError, read_product_details
from .models import ProductVariant
from .presentation import get_localized_value
from .pricing import discount_pricing


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SellingUnitPresentationDTO:
    code: str
    price_label: str
    singular_noun: str
    plural_noun: str

    def noun_for(self, quantity: int) -> str:
        return self.singular_noun if quantity == 1 else self.plural_noun


@dataclass(frozen=True, slots=True)
class CommerceAttributeDTO:
    definition_code: str
    label: str
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OperationalFactDTO:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class CommerceLinePresentationDTO:
    product_id: int
    product_slug: str
    product_url: str
    product_name: str
    image_url: str
    variant_id: int
    variant_sku: str
    variant_attributes: tuple[CommerceAttributeDTO, ...]
    product_attributes: tuple[CommerceAttributeDTO, ...]
    selling_unit: SellingUnitPresentationDTO
    quantity: int
    quantity_noun: str
    stock: int
    stock_noun: str
    is_low_stock: bool
    unit_price: Decimal
    subtotal: Decimal
    currency: str
    operational_facts: tuple[OperationalFactDTO, ...]
    source: str
    unit_reference_price: Decimal | None = None
    unit_savings: Decimal = Decimal("0")
    line_savings: Decimal = Decimal("0")
    discount_percent: int = 0


def _translated(value, *, language=None):
    if language:
        with override(language):
            return str(value())
    return str(value())


def selling_unit_presentation(code, *, language=None) -> SellingUnitPresentationDTO:
    """Resolve one model-owned PriceUnit code into localized UI semantics."""

    try:
        choice = ProductVariant.PriceUnit(code)
    except ValueError as error:
        raise ValueError(f"Unsupported ProductVariant price unit: {code!r}.") from error

    def resolved():
        if choice == ProductVariant.PriceUnit.UNIT:
            singular = ngettext("item", "items", 1)
            plural = ngettext("item", "items", 2)
        elif choice == ProductVariant.PriceUnit.BOX:
            singular = ngettext("box", "boxes", 1)
            plural = ngettext("box", "boxes", 2)
        elif choice == ProductVariant.PriceUnit.M2:
            singular = plural = _("m²")
        elif choice == ProductVariant.PriceUnit.TILE:
            singular = ngettext("tile", "tiles", 1)
            plural = ngettext("tile", "tiles", 2)
        elif choice == ProductVariant.PriceUnit.BAG:
            singular = ngettext("bag", "bags", 1)
            plural = ngettext("bag", "bags", 2)
        elif choice == ProductVariant.PriceUnit.BUCKET:
            singular = ngettext("bucket", "buckets", 1)
            plural = ngettext("bucket", "buckets", 2)
        elif choice == ProductVariant.PriceUnit.PACK:
            singular = ngettext("pack", "packs", 1)
            plural = ngettext("pack", "packs", 2)
        else:  # TextChoices is exhaustive; retain a defensive failure.
            raise ValueError(f"Unsupported ProductVariant price unit: {code!r}.")
        return SellingUnitPresentationDTO(
            code=choice.value,
            price_label=str(choice.label),
            singular_noun=str(singular),
            plural_noun=str(plural),
        )

    if language:
        with override(language):
            return resolved()
    return resolved()


def serialize_selling_unit(code, *, language=None):
    unit = selling_unit_presentation(code, language=language)
    return {
        "code": unit.code,
        "price_label": unit.price_label,
        "singular_noun": unit.singular_noun,
        "plural_noun": unit.plural_noun,
    }


def quantity_phrase(code, quantity, *, language=None):
    """Return a localized quantity plus noun, with a neutral unknown fallback."""

    quantity = int(quantity)
    if code:
        unit = selling_unit_presentation(code, language=language)
        noun = unit.noun_for(quantity)
    else:
        noun = _translated(
            lambda: ngettext("item", "items", quantity), language=language
        )
    template = _translated(lambda: _("%(quantity)s %(unit)s"), language=language)
    return template % {"quantity": quantity, "unit": noun}


def quantity_breakdown(unit_quantities, *, language=None, fallback_quantity=0):
    """Format per-selling-unit quantities without assigning one noun to a mixed order."""

    rendered = [
        quantity_phrase(code, quantity, language=language)
        for code, quantity in unit_quantities
        if quantity
    ]
    if not rendered and fallback_quantity:
        rendered.append(quantity_phrase(None, fallback_quantity, language=language))
    return ", ".join(rendered)


def _group_values(values):
    groups = OrderedDict()
    for value in values:
        if getattr(value, "is_internal", False):
            continue
        key = value.definition_code
        if key not in groups:
            groups[key] = {"label": value.label, "values": []}
        groups[key]["values"].append(value.display_value)
    return tuple(
        CommerceAttributeDTO(code, group["label"], tuple(group["values"]))
        for code, group in groups.items()
    )


def _group_product_attributes(groups):
    return tuple(
        CommerceAttributeDTO(
            group.definition_code,
            group.label,
            tuple(value.display_value for value in group.values),
        )
        for group in groups
        if group.values
    )


def _image_url(product):
    return product.image.url if product.image else ""


def _tile_compatibility_facts(product, variant, *, product_type_code, source, language=None):
    """Isolated legacy merchandising/operational facts; never part of the generic schema."""

    if product_type_code != "tile" and source != LEGACY_FALLBACK_SOURCE:
        return ()

    facts = []
    series = get_localized_value(product, "series_name", language=language)
    if series:
        facts.append(OperationalFactDTO(_translated(lambda: _("Series"), language=language), series))
    if variant.thickness_mm is not None:
        facts.append(OperationalFactDTO(
            _translated(lambda: _("Thickness"), language=language),
            f"{variant.thickness_mm} mm",
        ))
    if variant.coverage_per_box_m2 is not None:
        facts.append(OperationalFactDTO(
            _translated(lambda: _("Coverage"), language=language),
            f"{variant.coverage_per_box_m2} m² / {selling_unit_presentation(ProductVariant.PriceUnit.BOX, language=language).singular_noun}",
        ))
    if variant.tiles_per_box is not None:
        facts.append(OperationalFactDTO(
            _translated(lambda: _("Tiles per box"), language=language),
            str(variant.tiles_per_box),
        ))
    return tuple(facts)


def prepare_variant_operational_facts(
    product,
    variant,
    *,
    product_type_code,
    source="generic",
    language=None,
):
    """Return the existing narrow operational presentation for one Variant.

    Operational/packaging fields remain model-owned compatibility facts.  This
    public boundary lets Product Detail serialize the same prepared values as
    commerce lines without interpreting or querying them in JavaScript.
    """

    return _tile_compatibility_facts(
        product,
        variant,
        product_type_code=product_type_code,
        source=source,
        language=language,
    )


def _line_value(line, key):
    return line[key] if isinstance(line, dict) else getattr(line, key)


def _optional_line_value(line, key, default=None):
    """Read an additive presentation field without breaking older callers."""

    return line.get(key, default) if isinstance(line, dict) else getattr(line, key, default)


def prepare_commerce_line_presentations(lines: Iterable, *, currency=None, language=None):
    """Return Variant ID -> immutable presentation using a fixed-query generic batch."""

    line_list = list(lines)
    if not line_list:
        return {}
    products = list({
        _line_value(line, "product").pk: _line_value(line, "product")
        for line in line_list
    }.values())

    details = None
    try:
        details = read_product_details(products, language=language)
    except CatalogReadIntegrityError:
        # Transitional degradation is whole-line and isolated per Product below.
        details = {}

    result = {}
    for line in line_list:
        product = _line_value(line, "product")
        variant = _line_value(line, "variant")
        quantity = _line_value(line, "quantity")
        source = "generic"
        detail = details.get(product.pk)
        generic_variant = None
        if detail is not None:
            generic_variant = next((row for row in detail.variants if row.id == variant.pk), None)
        if generic_variant is None:
            try:
                detail = read_product_details([product], language=language)[product.pk]
                generic_variant = next(
                    (row for row in detail.variants if row.id == variant.pk), None
                )
                if generic_variant is None:
                    raise CatalogReadIntegrityError([
                        f"Variant ID {variant.pk} is absent from Product ID {product.pk}."
                    ])
            except CatalogReadIntegrityError as error:
                logger.error(
                    "generic_commerce_line_fallback product_id=%s variant_id=%s sku=%s cause=%s",
                    product.pk, variant.pk, variant.sku, error,
                    extra={
                        "event": "generic_commerce_line_fallback",
                        "product_id": product.pk,
                        "variant_id": variant.pk,
                        "variant_sku": variant.sku,
                        "catalog_read_cause": str(error),
                    },
                )
                fallback = compose_product_detail_presentation(
                    product, selected_variant=variant, language=language
                )
                source = fallback.source
                variant_attributes = _group_values(fallback.selected_variant_specifications)
                product_attributes = _group_product_attributes(fallback.card_attributes)
                product_type_code = fallback.product_type_code
            else:
                source = "generic"
        if source == "generic":
            specification_values = set(generic_variant.specification_attributes)
            variant_attributes = _group_values(
                value for value in generic_variant.attribute_values
                if (value.is_variant_dimension or value in specification_values)
                and not value.is_internal
            )
            product_attributes = _group_product_attributes(
                detail.product_attributes.card_attributes
            )
            product_type_code = detail.product_type_code

        selling_unit = selling_unit_presentation(variant.price_unit, language=language)
        line_currency = currency or _line_value(line, "currency")
        pricing = discount_pricing(
            selling_unit_price=_line_value(line, "unit_price"),
            reference_unit_price=_optional_line_value(line, "unit_reference_price"),
            quantity=quantity,
        )
        result[variant.pk] = CommerceLinePresentationDTO(
            product_id=product.pk,
            product_slug=product.slug,
            product_url=product.get_absolute_url(),
            product_name=get_localized_value(product, "name", language=language),
            image_url=_image_url(product),
            variant_id=variant.pk,
            variant_sku=variant.sku,
            variant_attributes=variant_attributes,
            product_attributes=product_attributes,
            selling_unit=selling_unit,
            quantity=quantity,
            quantity_noun=selling_unit.noun_for(quantity),
            stock=variant.stock,
            stock_noun=selling_unit.noun_for(variant.stock),
            is_low_stock=variant.is_low_stock,
            unit_price=pricing.selling_unit_price,
            unit_reference_price=pricing.reference_unit_price,
            unit_savings=pricing.unit_savings,
            line_savings=pricing.line_savings,
            discount_percent=pricing.discount_percent,
            subtotal=_line_value(line, "subtotal"),
            currency=line_currency,
            operational_facts=_tile_compatibility_facts(
                product, variant, product_type_code=product_type_code,
                source=source, language=language,
            ),
            source=source,
        )
    return result
