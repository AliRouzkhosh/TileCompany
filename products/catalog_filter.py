"""Schema-driven, side-effect-free catalog filtering and facet contracts."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Exists, F, FilteredRelation, Max, Min, OuterRef, Q, Subquery
from django.urls import reverse
from django.utils.translation import gettext as _

from .catalog_read import CatalogReadIntegrityError
from .models import (
    AttributeDefinition, AttributeOption, Brand, Category, ProductAttributeValue,
    ProductType, ProductTypeAttribute, ProductTypeAttributeOption, ProductVariant,
    VariantAttributeValue,
)
from .presentation import get_localized_value
from .templatetags.pricing import format_money
from .selectors import (
    _currency_value, _get_catalog_queryset,
    prefetch_variant_prices_for_currency,
)
from .search.engine import apply_search_terms_to_variants, search_products_in_queryset


logger = logging.getLogger(__name__)

TILE_LEGACY_FILTER_ALIASES = {
    "brand": ("brands", "brand"),
    "design": ("designs", "design"),
    "usage": ("usages", "usage"),
    "material": ("materials", "material"),
    "finish": ("finishes", "finish"),
    "color": ("colors", "color"),
    "size": ("sizes", "size"),
}

CATEGORY_FILTER_ALIASES = ("categories", "category")
PRODUCT_TYPE_FILTER_ALIASES = ("product_type",)


class CatalogFilterIntegrityError(CatalogReadIntegrityError):
    pass


def selected_category_slugs(request):
    """Normalize every supported Category query alias without losing repeats."""
    values = []
    for key in CATEGORY_FILTER_ALIASES:
        values.extend(
            value.strip() for value in request.GET.getlist(key) if value.strip()
        )
    return tuple(dict.fromkeys(values))


def selected_brand_slugs(request):
    """Normalize the canonical core Brand filter and its legacy plural alias."""
    values = []
    for key in ("brand", "brands"):
        values.extend(value.strip() for value in request.GET.getlist(key) if value.strip())
    return tuple(dict.fromkeys(values))


def selected_product_type_codes(request):
    """Normalize the canonical ProductType query identity without aliases."""
    return tuple(dict.fromkeys(
        value.strip()
        for key in PRODUCT_TYPE_FILTER_ALIASES
        for value in request.GET.getlist(key)
        if value.strip()
    ))


def build_brand_filter_options(products, *, selected_brands=(), language=None):
    """Return Brand facets from the caller's already narrowed Product universe."""
    selected = set(selected_brands)
    rows = (
        products.filter(brand__isnull=False)
        .order_by()
        .values("brand__slug", "brand__name", "brand__name_fa")
        .annotate(product_count=Count("pk", distinct=True))
        .order_by("brand__name", "brand__slug")
    )
    return tuple({
        "slug": row["brand__slug"],
        "label": row["brand__name_fa"] if language and language.startswith("fa") and row["brand__name_fa"] else row["brand__name"],
        "count": row["product_count"],
        "selected": row["brand__slug"] in selected,
    } for row in rows)


def build_product_type_filter_options(products, *, selected_codes=(), language=None):
    """Return represented ProductTypes from the caller's narrowed universe."""
    selected = set(selected_codes)
    rows = (
        products.filter(product_type__is_active=True)
        .order_by()
        .values("product_type__code", "product_type__name", "product_type__name_fa", "product_type__sort_order")
        .annotate(product_count=Count("pk", distinct=True))
        .order_by("product_type__sort_order", "product_type__name", "product_type__code")
    )
    return tuple({
        "code": row["product_type__code"],
        "label": row["product_type__name_fa"] if language and language.startswith("fa") and row["product_type__name_fa"] else row["product_type__name"],
        "count": row["product_count"],
        "selected": row["product_type__code"] in selected,
    } for row in rows)


@dataclass(frozen=True, slots=True)
class CatalogFilterSelectionDTO:
    definition_code: str
    scope: str
    data_type: str
    facet_mode: str
    values: tuple[str | bool, ...] = ()
    minimum: Decimal | None = None
    maximum: Decimal | None = None


@dataclass(frozen=True, slots=True)
class CatalogFilterResult:
    products: object
    matching_variants: object
    selections: tuple[CatalogFilterSelectionDTO, ...]
    facets: tuple[dict, ...]
    variant_scoped: bool
    source: str = "generic"
    brand_options: tuple[dict, ...] = ()
    selected_brands: tuple[str, ...] = ()
    product_type_options: tuple[dict, ...] = ()
    selected_product_types: tuple[str, ...] = ()
    all_facets: tuple[dict, ...] = ()


def active_descendant_ids(category):
    """Return an active category and all active descendants, cycle safely."""
    if not category or not category.is_active:
        return []
    children = {}
    for pk, parent_id in Category.objects.filter(is_active=True).values_list("pk", "parent_id"):
        children.setdefault(parent_id, []).append(pk)
    found, pending = set(), [category.pk]
    while pending:
        pk = pending.pop()
        if pk in found:
            continue
        found.add(pk)
        pending.extend(children.get(pk, ()))
    return sorted(found)


def _binding_map(products):
    type_ids = set(products.order_by().values_list("product_type_id", flat=True).distinct())
    if None in type_ids:
        raise CatalogFilterIntegrityError(["Catalog contains a Product without ProductType."])
    bindings = list(
        ProductTypeAttribute.objects.filter(
            product_type_id__in=type_ids, is_active=True, is_filterable=True,
            definition__is_active=True,
        ).select_related("definition", "product_type").order_by("sort_order", "id")
    )
    by_code = {}
    for binding in bindings:
        existing = by_code.get(binding.definition.code)
        if existing and (
            existing.scope != binding.scope
            or existing.definition.data_type != binding.definition.data_type
            or existing.facet_mode != binding.facet_mode
        ):
            raise CatalogFilterIntegrityError([
                f"Shared filter {binding.definition.code} has incompatible bindings."
            ])
        by_code.setdefault(binding.definition.code, binding)
    return bindings, by_code


def _decimal(value):
    try:
        return Decimal(value) if value not in (None, "") else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _is_internal_option(option):
    """Internal readiness/signature values are never public discovery values."""
    return bool((option.presentation_metadata or {}).get("internal"))


def normalize_filter_selections(request, products):
    """Normalize canonical and legacy parameters against applicable schema."""
    bindings, by_code = _binding_map(products)
    product_type_codes = set(
        products.order_by().values_list("product_type__code", flat=True).distinct()
    )
    selections = []
    for code, binding in by_code.items():
        definition = binding.definition
        if binding.facet_mode == ProductTypeAttribute.FacetMode.RANGE:
            minimum = _decimal(request.GET.get(f"attr_{code}_min"))
            maximum = _decimal(request.GET.get(f"attr_{code}_max"))
            if minimum is not None or maximum is not None:
                selections.append(CatalogFilterSelectionDTO(
                    code, binding.scope, definition.data_type, binding.facet_mode,
                    minimum=minimum, maximum=maximum,
                ))
            continue

        raw = [value.strip() for value in request.GET.getlist(f"attr_{code}") if value.strip()]
        if not raw and "tile" in product_type_codes and code in TILE_LEGACY_FILTER_ALIASES:
            for key in TILE_LEGACY_FILTER_ALIASES[code]:
                raw.extend(value.strip() for value in request.GET.getlist(key) if value.strip())
        if definition.data_type == AttributeDefinition.DataType.OPTION:
            valid = {
                option.code for option in AttributeOption.objects.filter(
                    definition_id=definition.pk, code__in=raw,
                ) if not _is_internal_option(option)
            }
            values = tuple(dict.fromkeys(value for value in raw if value in valid))
        elif definition.data_type == AttributeDefinition.DataType.BOOLEAN:
            normalized = {"true": True, "1": True, "false": False, "0": False}
            values = tuple(dict.fromkeys(normalized[value.lower()] for value in raw if value.lower() in normalized))
        else:
            values = ()
        if values:
            selections.append(CatalogFilterSelectionDTO(
                code, binding.scope, definition.data_type, binding.facet_mode, values=values,
            ))
    return tuple(selections), bindings


def _value_predicate(queryset, selection):
    queryset = queryset.filter(binding__definition__code=selection.definition_code)
    if selection.data_type == AttributeDefinition.DataType.OPTION:
        return queryset.filter(option_value__code__in=selection.values)
    if selection.data_type == AttributeDefinition.DataType.BOOLEAN:
        return queryset.filter(boolean_value__in=selection.values)
    carrier = "integer_value" if selection.data_type == AttributeDefinition.DataType.INTEGER else "decimal_value"
    if selection.minimum is not None:
        queryset = queryset.filter(**{f"{carrier}__gte": selection.minimum})
    if selection.maximum is not None:
        queryset = queryset.filter(**{f"{carrier}__lte": selection.maximum})
    return queryset


def _variant_candidates(selections, currency, *, search_query="", min_price="", max_price="", on_sale=None, in_stock=None):
    currency = _currency_value(currency)
    candidates = ProductVariant.objects.filter(is_active=True, product__is_active=True)
    if search_query:
        candidates = apply_search_terms_to_variants(candidates, search_query)
    for selection in selections:
        if selection.scope != ProductTypeAttribute.Scope.VARIANT:
            continue
        values = _value_predicate(
            VariantAttributeValue.objects.filter(variant_id=OuterRef("pk")), selection,
        )
        candidates = candidates.filter(Exists(values))
    candidates = candidates.annotate(
        catalog_currency_price=FilteredRelation(
            "prices", condition=Q(prices__currency=currency),
        ),
    ).annotate(
        catalog_price=F("catalog_currency_price__amount"),
        catalog_compare_at=F("catalog_currency_price__compare_at_amount"),
    )
    minimum, maximum = _decimal(min_price), _decimal(max_price)
    if minimum is not None:
        candidates = candidates.filter(catalog_price__gte=minimum)
    if maximum is not None:
        candidates = candidates.filter(catalog_price__lte=maximum)
    if on_sale == "1":
        candidates = candidates.filter(catalog_compare_at__gt=F("catalog_price"))
    if in_stock == "1":
        candidates = candidates.filter(stock__gt=0)
    return candidates


def apply_generic_filters(products, selections, currency, *, search_query="", min_price="", max_price="", on_sale=None, in_stock=None):
    """Apply AND-across definitions and same-Variant conjunction."""
    for selection in selections:
        if selection.scope != ProductTypeAttribute.Scope.PRODUCT:
            continue
        values = _value_predicate(
            ProductAttributeValue.objects.filter(product_id=OuterRef("pk")), selection,
        )
        products = products.filter(Exists(values))

    candidates = _variant_candidates(
        selections, currency, search_query=search_query, min_price=min_price, max_price=max_price,
        on_sale=on_sale, in_stock=in_stock,
    )
    # Product-scoped constraints (including a generic product attribute) must
    # constrain variant facet counts as well as the final Product queryset.
    candidates = candidates.filter(product__in=products)
    variant_scoped = any(
        selection.scope == ProductTypeAttribute.Scope.VARIANT for selection in selections
    ) or any((_decimal(min_price) is not None, _decimal(max_price) is not None, on_sale == "1", in_stock == "1"))

    # Preserve the established catalog rule that public Products need an active Variant.
    products = products.filter(Exists(candidates.filter(product_id=OuterRef("pk"))))
    if variant_scoped:
        priced = candidates.filter(product_id=OuterRef("pk"), catalog_price__isnull=False)
        # F3B qualifying constraints win over a search-specific target.
        target = priced.order_by("catalog_price", "sort_order", "id")
        products = products.annotate(
            catalog_pricing_is_scoped=Exists(priced),
            catalog_matching_variant_count=Subquery(
                candidates.filter(product_id=OuterRef("pk")).order_by().values("product_id")
                .annotate(total=Count("pk")).values("total")[:1]
            ),
            catalog_min_price=Subquery(target.values("catalog_price")[:1]),
            catalog_max_price=Subquery(priced.order_by("-catalog_price", "id").values("catalog_price")[:1]),
            catalog_target_variant_id=Subquery(target.values("id")[:1]),
            catalog_sale_percent=Subquery(
                target.annotate(percent=(F("catalog_compare_at") - F("catalog_price")) * 100 / F("catalog_compare_at"))
                .values("percent")[:1]
            ),
        )
    return products.distinct(), prefetch_variant_prices_for_currency(candidates, currency), variant_scoped


def _allowed_option_ids(bindings):
    allowed = set()
    for binding in bindings:
        restrictions = list(ProductTypeAttributeOption.objects.filter(
            binding=binding, is_active=True, option__is_active=True,
        ).values_list("option_id", flat=True))
        if restrictions:
            allowed.update(restrictions)
        else:
            allowed.update(AttributeOption.objects.filter(
                definition=binding.definition, is_active=True,
            ).values_list("id", flat=True))
    return allowed


def _facet_usefulness(products, binding):
    """Return product-level coverage and distinct public values for one binding.

    This is deliberately bounded by schema bindings, never by individual product
    or option rows.  Variant values are counted by their owning Product.
    """
    definition = binding.definition
    if binding.scope == ProductTypeAttribute.Scope.PRODUCT:
        values = ProductAttributeValue.objects.filter(
            product__in=products, binding=binding,
        )
        owner = "product_id"
    else:
        values = VariantAttributeValue.objects.filter(
            variant__product__in=products, binding=binding,
        )
        owner = "variant__product_id"
    if definition.data_type == AttributeDefinition.DataType.OPTION:
        internal_option_ids = [
            option.pk for option in AttributeOption.objects.filter(definition=definition)
            if _is_internal_option(option)
        ]
        if internal_option_ids:
            values = values.exclude(option_value_id__in=internal_option_ids)
        value_field = "option_value_id"
    elif definition.data_type == AttributeDefinition.DataType.BOOLEAN:
        value_field = "boolean_value"
    else:
        value_field = (
            "integer_value" if definition.data_type == AttributeDefinition.DataType.INTEGER
            else "decimal_value"
        )
    stats = values.aggregate(
        populated=Count(owner, distinct=True),
        distinct_values=Count(value_field, distinct=True),
    )
    applicable = products.order_by().values("pk").distinct().count()
    populated = stats["populated"] or 0
    return {
        "applicable_products": applicable,
        "populated_products": populated,
        "distinct_values": stats["distinct_values"] or 0,
        "coverage": (populated / applicable) if applicable else 0,
    }


def build_generic_facets(base_products, selections, bindings, currency, *, search_query="", min_price="", max_price="", on_sale=None, in_stock=None, language=None):
    """Build one grouped count query per facet, excluding that facet's selection."""
    by_code = {}
    for binding in bindings:
        by_code.setdefault(binding.definition.code, []).append(binding)
    facets = []
    for code, code_bindings in sorted(by_code.items(), key=lambda item: (item[1][0].sort_order, item[0])):
        binding = code_bindings[0]
        definition = binding.definition
        other = tuple(selection for selection in selections if selection.definition_code != code)
        other_products, other_variants, other_is_scoped = apply_generic_filters(
            base_products, other, currency, search_query=search_query, min_price=min_price, max_price=max_price,
            on_sale=on_sale, in_stock=in_stock,
        )
        selected = next((item for item in selections if item.definition_code == code), None)
        facet = {
            "definition_code": code,
            "label": get_localized_value(definition, "name", language=language),
            "scope": binding.scope, "data_type": definition.data_type,
            "facet_mode": binding.facet_mode, "unit": definition.unit,
            "sort_order": binding.sort_order, "choices": [],
            "selected_min": selected.minimum if selected else None,
            "selected_max": selected.maximum if selected else None,
        }
        facet.update(_facet_usefulness(base_products, binding))
        if binding.facet_mode == ProductTypeAttribute.FacetMode.RANGE:
            carrier = "integer_value" if definition.data_type == AttributeDefinition.DataType.INTEGER else "decimal_value"
            values = (ProductAttributeValue.objects.filter(product__in=other_products)
                      if binding.scope == ProductTypeAttribute.Scope.PRODUCT
                      else VariantAttributeValue.objects.filter(variant__in=other_variants))
            bounds = values.filter(binding__definition=definition).aggregate(
                available_min=Min(carrier), available_max=Max(carrier),
            )
            facet.update(bounds)
        else:
            if binding.scope == ProductTypeAttribute.Scope.PRODUCT:
                values = ProductAttributeValue.objects.filter(
                    product__in=other_products, binding__definition=definition,
                )
                owner = "product_id"
            else:
                values = VariantAttributeValue.objects.filter(
                    variant__in=other_variants, binding__definition=definition,
                )
                owner = "variant__product_id"
            if definition.data_type == AttributeDefinition.DataType.OPTION:
                counts = dict(values.values_list("option_value_id").annotate(total=Count(owner, distinct=True)))
                represented = set(counts)
                allowed_ids = _allowed_option_ids(code_bindings)
                selected_values = set(selected.values if selected else ())
                selected_ids = set(AttributeOption.objects.filter(
                    definition=definition, code__in=selected_values,
                ).values_list("pk", flat=True))
                option_ids = allowed_ids | represented | selected_ids
                options = AttributeOption.objects.filter(pk__in=option_ids, definition=definition).order_by("sort_order", "name", "id")
                facet["choices"] = [{
                    "code": option.code,
                    "label": get_localized_value(option, "name", language=language),
                    "count": counts.get(option.pk, 0),
                    "selected": option.code in selected_values,
                    "disabled": False,
                    "is_retired": not option.is_active or option.pk not in allowed_ids,
                } for option in options if not _is_internal_option(option) and (
                    counts.get(option.pk, 0) > 0 or option.code in selected_values
                )]
            else:
                counts = dict(values.values_list("boolean_value").annotate(total=Count(owner, distinct=True)))
                selected_values = set(selected.values if selected else ())
                facet["choices"] = [{
                    "code": "true" if value else "false",
                    "label": str(_("Yes")) if value else str(_("No")), "count": counts.get(value, 0),
                    "selected": value in selected_values,
                    "disabled": False,
                    "is_retired": False,
                } for value in (True, False) if counts.get(value, 0) or value in selected_values]
        if selected is None:
            facet["selected_count"] = 0
        elif binding.facet_mode == ProductTypeAttribute.FacetMode.RANGE:
            facet["selected_count"] = int(selected.minimum is not None) + int(selected.maximum is not None)
        else:
            facet["selected_count"] = len(selected.values)
        facet["is_eligible"] = (
            facet["distinct_values"] >= 2
            and facet["coverage"] >= 0.60
        )
        facets.append(facet)
    return tuple(facets)


def build_generic_catalog(request, currency, *, category=None, language=None):
    """Compose filtered Products, matching Variants, and schema facets."""
    products = _get_catalog_queryset(currency)
    products = search_products_in_queryset(
        products, request.GET.get("q", "").strip(), currency=currency,
    )
    if category:
        products = products.filter(categories__id__in=active_descendant_ids(category)).distinct()
    selected_categories = selected_category_slugs(request)
    if selected_categories:
        category_ids = set()
        for selected in Category.objects.filter(slug__in=selected_categories, is_active=True):
            category_ids.update(active_descendant_ids(selected))
        products = products.filter(categories__id__in=category_ids).distinct()
    base_products = products
    selected_product_types = selected_product_type_codes(request)
    product_type_options = build_product_type_filter_options(
        base_products, selected_codes=selected_product_types, language=language,
    )
    if selected_product_types:
        products = products.filter(product_type__code__in=selected_product_types).distinct()
    schema_products = products
    selected_brands = selected_brand_slugs(request)
    brand_options = build_brand_filter_options(
        products, selected_brands=selected_brands, language=language,
    )
    if selected_brands:
        products = products.filter(brand__slug__in=selected_brands).distinct()
    # Selection validation uses the category/search/type universe.  It remains
    # independent of presentation eligibility so bookmarked generic deep links
    # continue to filter broad heterogeneous pages.
    selections, bindings = normalize_filter_selections(request, schema_products)
    # Core price/availability constraints are part of the current presentation
    # universe; schema selections are deliberately excluded so an active deep
    # link cannot turn a broad page into a newly discoverable schema sidebar.
    facet_universe, _, _ = apply_generic_filters(
        products, (), currency,
        search_query=request.GET.get("q", "").strip(),
        min_price=request.GET.get("min_price", ""), max_price=request.GET.get("max_price", ""),
        on_sale=request.GET.get("on_sale"), in_stock=request.GET.get("in_stock"),
    )
    products, variants, scoped = apply_generic_filters(
        products, selections, currency,
        search_query=request.GET.get("q", "").strip(),
        min_price=request.GET.get("min_price", ""), max_price=request.GET.get("max_price", ""),
        on_sale=request.GET.get("on_sale"), in_stock=request.GET.get("in_stock"),
    )
    all_facets = build_generic_facets(
        facet_universe, selections, bindings, currency,
        search_query=request.GET.get("q", "").strip(),
        min_price=request.GET.get("min_price", ""), max_price=request.GET.get("max_price", ""),
        on_sale=request.GET.get("on_sale"), in_stock=request.GET.get("in_stock"), language=language,
    )
    represented_type_count = facet_universe.order_by().values("product_type_id").distinct().count()
    facets = tuple(
        facet for facet in all_facets
        if represented_type_count == 1 and facet["is_eligible"]
    )
    return CatalogFilterResult(
        products, variants, selections, facets, scoped,
        brand_options=brand_options, selected_brands=selected_brands,
        product_type_options=product_type_options,
        selected_product_types=selected_product_types,
        all_facets=all_facets,
    )


def _filter_url(request, *, remove_keys=(), remove_value=None, clear=False, path=None):
    params = request.GET.copy()
    params.pop("page", None)
    if clear:
        legacy_keys = {
            key for keys in TILE_LEGACY_FILTER_ALIASES.values() for key in keys
        }
        for key in list(params):
            if key.startswith("attr_") or key in legacy_keys | {
                "q", "categories", "category", "product_type", "brand", "brands", "min_price", "max_price", "on_sale", "in_stock",
            }:
                params.pop(key, None)
    else:
        for key in remove_keys:
            if remove_value is None:
                params.pop(key, None)
            else:
                remaining = [value for value in params.getlist(key) if value != remove_value]
                params.setlist(key, remaining)
                if not remaining:
                    params.pop(key, None)
    target = path or request.path
    query = params.urlencode()
    return f"{target}?{query}" if query else target


def build_generic_filter_state(request, result, currency, *, category=None, query=""):
    """Build dynamic chips and QueryDict-safe removal links."""
    chips = []
    active_filter_count = 0

    def add(label, display, keys, raw=None, path=None, *, counts_as_filter=False):
        nonlocal active_filter_count
        chips.append({
            "label": label, "display_value": display,
            "remove_url": _filter_url(request, remove_keys=keys, remove_value=raw, path=path),
            "remove_label": _("Remove filter: %(label)s %(value)s") % {
                "label": label, "value": display,
            },
        })
        if counts_as_filter:
            active_filter_count += 1

    base_path = reverse("products:product_list")
    if category:
        add(_("Category"), get_localized_value(category, "name"), (), path=base_path)
    selected_categories = selected_category_slugs(request)
    if selected_categories:
        categories = {
            item.slug: item
            for item in Category.objects.filter(
                slug__in=selected_categories, is_active=True
            )
        }
        for slug in selected_categories:
            selected_category = categories.get(slug)
            if selected_category:
                add(
                    _("Category"),
                    get_localized_value(selected_category, "name"),
                    CATEGORY_FILTER_ALIASES,
                    slug,
                    counts_as_filter=True,
                )
    if query:
        add(_("Search"), query, ("q",))
    brands = {brand["slug"]: brand for brand in result.brand_options}
    brands.update({
        brand.slug: {"slug": brand.slug, "label": get_localized_value(brand, "name")}
        for brand in Brand.objects.filter(slug__in=result.selected_brands)
    })
    for slug in result.selected_brands:
        brand = brands.get(slug)
        if brand:
            add(_("Brand"), brand["label"], ("brand", "brands"), slug, counts_as_filter=True)
    product_types = {item["code"]: item for item in result.product_type_options}
    product_types.update({
        product_type.code: {"code": product_type.code, "label": get_localized_value(product_type, "name")}
        for product_type in ProductType.objects.filter(code__in=result.selected_product_types)
    })
    for code in result.selected_product_types:
        product_type = product_types.get(code)
        if product_type:
            # Reuse the established gettext identity so existing compiled FA
            # catalogs render this core label without an English fallback.
            add(_("Product type"), product_type["label"], PRODUCT_TYPE_FILTER_ALIASES, code, counts_as_filter=True)
    # `all_facets` intentionally includes valid active deep-link attributes
    # suppressed from discovery on a heterogeneous result page.
    facets = {facet["definition_code"]: facet for facet in result.all_facets}
    for selection in result.selections:
        facet = facets.get(selection.definition_code)
        if not facet:
            continue
        canonical = f"attr_{selection.definition_code}"
        aliases = TILE_LEGACY_FILTER_ALIASES.get(selection.definition_code, ())
        if selection.facet_mode == ProductTypeAttribute.FacetMode.RANGE:
            if selection.minimum is not None:
                add(facet["label"], f"≥ {selection.minimum} {facet['unit']}".strip(), (f"{canonical}_min",), counts_as_filter=True)
            if selection.maximum is not None:
                add(facet["label"], f"≤ {selection.maximum} {facet['unit']}".strip(), (f"{canonical}_max",), counts_as_filter=True)
            continue
        choice_labels = {choice["code"]: choice["label"] for choice in facet["choices"]}
        for value in selection.values:
            code = "true" if value is True else "false" if value is False else value
            add(facet["label"], choice_labels.get(code, str(code)), (canonical, *aliases), str(code), counts_as_filter=True)
    for key, label in (("min_price", _("Minimum price")), ("max_price", _("Maximum price"))):
        value = request.GET.get(key, "")
        if _decimal(value) is not None:
            add(label, format_money(value, currency), (key,), counts_as_filter=True)
    if request.GET.get("in_stock") == "1":
        add(_("In Stock"), "", ("in_stock",), counts_as_filter=True)
    if request.GET.get("on_sale") == "1":
        add(_("On Sale"), "", ("on_sale",), counts_as_filter=True)
    return {
        "active_filter_chips": chips,
        "active_filter_count": active_filter_count,
        "has_active_criteria": bool(chips),
        "clear_all_url": _filter_url(request, clear=True, path=base_path),
    }
