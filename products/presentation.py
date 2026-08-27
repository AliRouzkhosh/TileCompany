"""Read-only presentation helpers for database-backed localized content."""

from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext as _

from products.templatetags.pricing import format_money


def get_localized_value(obj, field_name, *, language=None):
    """Return the Persian sibling field when the active language is Persian.

    Values are resolved from fields already loaded on ``obj``; this helper never
    queries or writes to the database. A blank Persian value falls back to the
    default field.
    """
    default_value = getattr(obj, field_name, "")
    active_language = (language or get_language() or "").lower().replace("_", "-")
    if not active_language.startswith("fa"):
        return default_value

    persian_value = getattr(obj, f"{field_name}_fa", None)
    if isinstance(persian_value, str):
        return persian_value if persian_value.strip() else default_value
    return persian_value if persian_value is not None else default_value


CATALOG_NARROWING_KEYS = (
    "q", "categories", "category", "brands", "brand", "designs", "design",
    "usages", "usage", "materials", "material", "finishes", "finish",
    "colors", "color", "sizes", "size", "min_price", "max_price",
    "on_sale", "in_stock",
)


def _catalog_url(request, *, path=None, remove_keys=(), remove_value=None, clear=False):
    params = request.GET.copy()
    params.pop("page", None)
    if clear:
        for key in CATALOG_NARROWING_KEYS:
            params.pop(key, None)
    else:
        for key in remove_keys:
            if remove_value is None:
                params.pop(key, None)
                continue
            remaining = [value for value in params.getlist(key) if value != remove_value]
            params.setlist(key, remaining)
            if not remaining:
                params.pop(key, None)
    target = path or request.path
    query_string = params.urlencode()
    return f"{target}?{query_string}" if query_string else target


def build_catalog_filter_state(
    request, *, category, query, currency, filter_options, selected_filters,
    min_price="", max_price="", on_sale=None, in_stock=None,
):
    """Build localized active-filter labels and robust removal URLs."""
    chips = []
    active_filter_count = 0

    def add_chip(label, display_value, keys, raw_value=None, *, path=None, counts_as_filter=False):
        nonlocal active_filter_count
        accessible_value = f" {display_value}" if display_value else ""
        chips.append({
            "label": label,
            "display_value": display_value,
            "remove_url": _catalog_url(
                request, path=path, remove_keys=keys, remove_value=raw_value,
            ),
            "remove_label": _("Remove filter: %(label)s%(value)s") % {
                "label": label,
                "value": accessible_value,
            },
        })
        if counts_as_filter:
            active_filter_count += 1

    base_path = reverse("products:product_list")
    if category:
        add_chip(_("Category"), get_localized_value(category, "name"), (), path=base_path)
    if query:
        add_chip(_("Search"), query, ("q",))

    definitions = (
        ("categories", "category", _("Category"), "categories"),
        ("brands", "brand", _("Brand"), "brands"),
        ("designs", "design", _("Design"), "designs"),
        ("usages", "usage", _("Usage"), "usages"),
        ("materials", "material", _("Material"), "materials"),
        ("finishes", "finish", _("Finish"), "finishes"),
        ("colors", "color", _("Color"), "colors"),
        ("sizes", "size", _("Size"), "sizes"),
    )
    for plural_key, singular_key, label, option_key in definitions:
        options = {item.slug: item for item in filter_options[option_key]}
        for value in selected_filters[plural_key]:
            item = options.get(value)
            if item:
                add_chip(
                    label, get_localized_value(item, "name"),
                    (plural_key, singular_key), value, counts_as_filter=True,
                )

    if min_price:
        try:
            add_chip(_("Minimum price"), format_money(min_price, currency), ("min_price",), counts_as_filter=True)
        except (ArithmeticError, ValueError):
            pass
    if max_price:
        try:
            add_chip(_("Maximum price"), format_money(max_price, currency), ("max_price",), counts_as_filter=True)
        except (ArithmeticError, ValueError):
            pass
    if in_stock == "1":
        add_chip(_("In Stock"), "", ("in_stock",), counts_as_filter=True)
    if on_sale == "1":
        add_chip(_("On Sale"), "", ("on_sale",), counts_as_filter=True)

    return {
        "active_filter_chips": chips,
        "active_filter_count": active_filter_count,
        "has_active_criteria": bool(chips),
        "clear_all_url": _catalog_url(request, path=base_path, clear=True),
    }
