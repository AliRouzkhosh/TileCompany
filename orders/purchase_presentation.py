"""Purchase-history presentation built exclusively from frozen snapshots."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from django.utils.translation import get_language, gettext as _
from products.pricing import discount_pricing

from products.purchase_snapshot import (
    PurchaseSnapshotIntegrityError,
    UnsupportedPurchaseSnapshotVersion,
    parse_purchase_snapshot,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PurchasedAttributePresentationDTO:
    definition_code: str
    label: str
    values: tuple[str, ...]
    is_dimension: bool


@dataclass(frozen=True, slots=True)
class PurchasedOperationalFactPresentationDTO:
    code: str
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class PurchasedSellingUnitPresentationDTO:
    code: str
    price_label: str
    singular_noun: str
    plural_noun: str

    def noun_for(self, quantity):
        return self.singular_noun if quantity == 1 else self.plural_noun


@dataclass(frozen=True, slots=True)
class PurchasedLinePresentationDTO:
    source: str
    version: int | None
    status: str
    product_name: str
    unit_price: object
    unit_reference_price: object
    unit_savings: object
    discount_percent: int
    product_type_code: str
    product_type_label: str
    variant_sku: str
    selling_unit: PurchasedSellingUnitPresentationDTO | None
    quantity_noun: str
    product_attributes: tuple[PurchasedAttributePresentationDTO, ...]
    variant_attributes: tuple[PurchasedAttributePresentationDTO, ...]
    operational_facts: tuple[PurchasedOperationalFactPresentationDTO, ...]
    specification_chips: tuple[str, ...]
    current_product_url: str
    customer_diagnostic: str
    operator_diagnostic: str

    @property
    def has_details(self):
        return bool(
            self.product_attributes
            or self.variant_attributes
            or self.operational_facts
        )

    @property
    def has_diagnostic(self):
        return self.status != "ok"


LEGACY_LABELS = {
    "size": lambda: _("Size"),
    "material": lambda: _("Material"),
    "finish": lambda: _("Finish"),
    "color": lambda: _("Color"),
    "thickness": lambda: _("Thickness"),
    "coverage": lambda: _("Coverage"),
    "tiles_per_box": lambda: _("Tiles per box"),
}


def _is_persian(language):
    return (language or "").lower().replace("_", "-").startswith("fa")


def _localized(default, fa, *, language):
    default = str(default or "")
    fa = str(fa or "").strip()
    return (fa or default) if _is_persian(language) else default


def _attribute_presentation(attribute, *, language, legacy=False):
    if legacy:
        label_factory = LEGACY_LABELS.get(attribute.definition_code)
        label = str(label_factory()) if label_factory else attribute.label_default
        values = tuple(value.display_default for value in attribute.values)
    else:
        label = _localized(
            attribute.label_default, attribute.label_fa, language=language
        )
        values = tuple(
            _localized(
                value.display_default, value.display_fa, language=language
            )
            for value in attribute.values
            if not bool(getattr(value, "is_internal", False))
        )
        if not values:
            return None
    return PurchasedAttributePresentationDTO(
        definition_code=attribute.definition_code,
        label=label,
        values=values,
        is_dimension=attribute.is_dimension,
    )


def purchase_history_item_queryset():
    """Load all optional live compatibility data before template rendering."""

    # Keep the model import local: OrderItem delegates presentation back to this
    # module, so a module-level import would create a circular dependency.
    from .models import OrderItem

    return OrderItem.objects.select_related(
        "product",
        "variant",
        "variant__product",
        "variant__color",
        "variant__size",
        "variant__finish",
    ).prefetch_related(
        "product__materials",
        "variant__product__materials",
    )


def _cached_current_product(item):
    """Return a prepared live Product without ever consulting the database."""

    product = item._state.fields_cache.get("product")
    if product is not None:
        return product
    variant = item._state.fields_cache.get("variant")
    if variant is not None:
        return variant._state.fields_cache.get("product")
    return None


def _normalized_legacy_text(value):
    return " ".join(str(value or "").split()).casefold()


def _legacy_relation_value(item, code, frozen_value):
    variant = item._state.fields_cache.get("variant")
    if variant is None:
        return frozen_value
    relation = variant._state.fields_cache.get(code)
    if relation is None:
        return frozen_value
    if _normalized_legacy_text(relation.name) != _normalized_legacy_text(frozen_value):
        # The snapshot is authoritative. A renamed/reassigned current relation
        # is not evidence that historical purchase truth changed.
        return frozen_value
    return str(relation.name_fa or "").strip() or frozen_value


def _legacy_material_value(item, frozen_value):
    product = _cached_current_product(item)
    if product is None:
        return frozen_value
    materials = getattr(product, "_prefetched_objects_cache", {}).get("materials")
    if materials is None:
        return frozen_value

    by_default_name = {
        _normalized_legacy_text(material.name): material for material in materials
    }
    frozen_parts = [part.strip() for part in str(frozen_value).split(",")]
    localized_parts = []
    for part in frozen_parts:
        material = by_default_name.get(_normalized_legacy_text(part))
        if material is None:
            return frozen_value
        localized_parts.append(str(material.name_fa or "").strip() or part)
    return ", ".join(localized_parts)


def _legacy_attribute_presentation(item, attribute, *, language):
    presentation = _attribute_presentation(
        attribute, language=language, legacy=True
    )
    if not _is_persian(language):
        return presentation

    values = []
    for frozen_value in presentation.values:
        if attribute.definition_code == "material":
            values.append(_legacy_material_value(item, frozen_value))
        elif attribute.definition_code in {"color", "size", "finish"}:
            values.append(
                _legacy_relation_value(
                    item, attribute.definition_code, frozen_value
                )
            )
        else:
            values.append(frozen_value)
    return PurchasedAttributePresentationDTO(
        definition_code=presentation.definition_code,
        label=presentation.label,
        values=tuple(values),
        is_dimension=presentation.is_dimension,
    )


def _localized_legacy_operational_value(code, value, *, language):
    value = str(value)
    if not _is_persian(language):
        return value

    patterns = {
        "thickness": re.compile(
            r"^\s*(?P<value>.+?)\s*mm\s*$", re.IGNORECASE
        ),
        "coverage": re.compile(
                r"^\s*(?P<value>.+?)\s*m(?:\u00c2)?\u00b2\s*/\s*box\s*$",
                re.IGNORECASE,
        ),
        "tiles_per_box": re.compile(
                r"^\s*(?P<value>.+?)\s*tiles?\s*/\s*box\s*$",
                re.IGNORECASE,
        ),
    }
    match = patterns[code].match(value)
    if match is None:
        return value
    number = match.group("value")
    if code == "thickness":
        return str(_("%(value)s millimeters")) % {"value": number}
    if code == "coverage":
        return str(_("%(value)s square meters per box")) % {"value": number}
    return str(_("%(value)s tiles per box")) % {"value": number}


def _legacy_operational_facts(parsed, *, language):
    facts = []
    for code in ("thickness", "coverage", "tiles_per_box"):
        value = parsed.operational.get(code)
        if value:
            facts.append(
                PurchasedOperationalFactPresentationDTO(
                    code=code,
                    label=str(LEGACY_LABELS[code]()),
                    value=_localized_legacy_operational_value(
                        code, value, language=language
                    ),
                )
            )
    return tuple(facts)


def _v2_operational_facts(parsed):
    """Normalize the currently approved optional Tile compatibility block."""

    tile = parsed.operational.get("tile")
    if not tile:
        return ()
    display = tile.get("display", {})
    values = {
        "thickness": display.get("thickness"),
        "coverage": display.get("coverage"),
        "tiles_per_box": display.get("tiles_per_box"),
    }
    facts = []
    for code in ("thickness", "coverage", "tiles_per_box"):
        if values[code]:
            facts.append(
                PurchasedOperationalFactPresentationDTO(
                    code=code,
                    label=str(LEGACY_LABELS[code]()),
                    value=str(values[code]),
                )
            )
    return tuple(facts)


def _selling_unit(parsed, *, language):
    if parsed.source != "v2":
        return None
    unit = parsed.variant["selling_unit"]
    return PurchasedSellingUnitPresentationDTO(
        code=str(unit["code"]),
        price_label=_localized(
            unit["price_label_default"], unit["price_label_fa"], language=language
        ),
        singular_noun=_localized(
            unit["singular_noun_default"], unit["singular_noun_fa"], language=language
        ),
        plural_noun=_localized(
            unit["plural_noun_default"], unit["plural_noun_fa"], language=language
        ),
    )


def _safe_snapshot_sku(snapshot):
    if not isinstance(snapshot, dict):
        return ""
    if isinstance(snapshot.get("sku"), str):
        return snapshot["sku"]
    variant = snapshot.get("variant")
    if isinstance(variant, dict) and isinstance(variant.get("sku"), str):
        return variant["sku"]
    return ""


def _current_product_url(item):
    # Do not trigger a relation query. A current link is optional decoration.
    product = item._state.fields_cache.get("product")
    if product is None or not product.is_active:
        return ""
    return product.get_absolute_url()


def _cached_legacy_variant_sku(item):
    variant = item._state.fields_cache.get("variant")
    return variant.sku if variant is not None else ""


def _diagnostic_presentation(item, error, *, source, version):
    logger.error(
        "purchase_snapshot_presentation_error order_id=%s order_item_id=%s "
        "source=%s version=%r cause=%s: %s",
        item.order_id,
        item.pk,
        source,
        version,
        type(error).__name__,
        error,
    )
    snapshot = item.variant_snapshot
    return PurchasedLinePresentationDTO(
        source=source,
        version=version,
        status="unsupported" if isinstance(error, UnsupportedPurchaseSnapshotVersion) else "malformed",
        product_name=item.product_name,
        unit_price=item.unit_price,
        unit_reference_price=None,
        unit_savings=0,
        discount_percent=0,
        product_type_code="",
        product_type_label="",
        variant_sku=_safe_snapshot_sku(snapshot),
        selling_unit=None,
        quantity_noun="",
        product_attributes=(),
        variant_attributes=(),
        operational_facts=(),
        specification_chips=(),
        current_product_url=_current_product_url(item),
        customer_diagnostic=str(_("Purchase details unavailable")),
        operator_diagnostic=str(_("Purchase snapshot requires review")),
    )


def build_purchased_line_presentation(item, *, language=None):
    """Prepare one immutable, zero-query history presentation."""

    language = language or get_language() or "en"
    snapshot = item.variant_snapshot
    version = snapshot.get("version") if isinstance(snapshot, dict) else None
    try:
        parsed = parse_purchase_snapshot(snapshot)
        if (
            parsed.source == "legacy"
            and snapshot
            and not parsed.variant.get("sku")
            and not parsed.product_attributes
            and not parsed.variant_attributes
            and not parsed.operational
        ):
            raise PurchaseSnapshotIntegrityError(
                "Legacy snapshot contains no recognized purchase fields."
            )
    except (PurchaseSnapshotIntegrityError, TypeError, ValueError) as error:
        source = "unknown" if version is not None else "malformed"
        return _diagnostic_presentation(
            item, error, source=source, version=version
        )

    legacy = parsed.source == "legacy"
    product_attributes = tuple(
        (
            _legacy_attribute_presentation(item, attribute, language=language)
            if legacy
            else _attribute_presentation(attribute, language=language)
        )
        for attribute in parsed.product_attributes
    )
    product_attributes = tuple(attribute for attribute in product_attributes if attribute is not None)
    variant_attributes = tuple(
        (
            _legacy_attribute_presentation(item, attribute, language=language)
            if legacy
            else _attribute_presentation(attribute, language=language)
        )
        for attribute in parsed.variant_attributes
    )
    variant_attributes = tuple(attribute for attribute in variant_attributes if attribute is not None)
    operational_facts = (
        _legacy_operational_facts(parsed, language=language)
        if legacy
        else _v2_operational_facts(parsed)
    )
    selling_unit = _selling_unit(parsed, language=language)

    if legacy:
        current_product = _cached_current_product(item)
        product_name = (
            str(current_product.name_fa or "").strip()
            if _is_persian(language) and current_product is not None
            else ""
        ) or item.product_name
        product_type_code = ""
        product_type_label = ""
    else:
        product_name = _localized(
            parsed.product["name_default"],
            parsed.product["name_fa"],
            language=language,
        ) or item.product_name
        product_type = parsed.product["product_type"]
        product_type_code = str(product_type["code"])
        product_type_label = _localized(
            product_type["label_default"],
            product_type["label_fa"],
            language=language,
        )

    chips = tuple(
        value
        for attribute in (*product_attributes, *variant_attributes)
        for value in attribute.values
    )
    pricing = discount_pricing(
        selling_unit_price=item.unit_price,
        reference_unit_price=item.unit_reference_price,
        quantity=item.quantity,
    )
    return PurchasedLinePresentationDTO(
        source=parsed.source,
        version=parsed.version,
        status="ok",
        product_name=product_name,
        unit_price=pricing.selling_unit_price,
        unit_reference_price=pricing.reference_unit_price,
        unit_savings=pricing.unit_savings,
        discount_percent=pricing.discount_percent,
        product_type_code=product_type_code,
        product_type_label=product_type_label,
        variant_sku=(
            str(parsed.variant.get("sku", ""))
            or (_cached_legacy_variant_sku(item) if legacy else "")
        ),
        selling_unit=selling_unit,
        quantity_noun=(selling_unit.noun_for(item.quantity) if selling_unit else ""),
        product_attributes=product_attributes,
        variant_attributes=variant_attributes,
        operational_facts=operational_facts,
        specification_chips=chips,
        current_product_url=_current_product_url(item),
        customer_diagnostic="",
        operator_diagnostic="",
    )
