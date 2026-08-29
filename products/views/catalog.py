################ products/views/catalog.py #################
import logging

from django.shortcuts import render, get_object_or_404
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.utils.cache import patch_cache_control
from django.urls import reverse
from django.utils.translation import get_language, gettext as _
from TileCompany.htmx import is_htmx

from products.search.autocomplete import autocomplete
from products.models import Category, Product, ProductVariant, ProductView
from reviews.forms import ReviewCommentForm
from products.storefront import get_storefront_currency
from products.presentation import build_catalog_filter_state, get_localized_value
from products.catalog_presentation import (
    compose_product_detail_presentation,
    prepare_product_card_presentations,
)
from products.catalog_filter import (
    CatalogFilterIntegrityError,
    build_generic_catalog,
    build_category_selector_items,
    get_category_selector_categories,
    build_generic_filter_state,
    selected_category_slugs,
)
from products.storefront_navigation import prepare_storefront_discovery

from products.selectors import (
    get_featured_products,
    get_home_catalog_categories,
    get_home_application_areas,
    get_home_brands,
    attach_home_merchandising_media,
    get_home_merchandising_products,
    get_homepage_hero,
    get_homepage_editorial_article,
    get_on_sale_products,
    get_product_filter_data,
    get_sorted_products,
    get_filtered_products,
    attach_target_variant_ids,
    has_active_variant_filters,
    get_product_by_slug,
    get_product_review_queryset,
    get_related_products,
    is_product_wishlisted,
    get_review_context,
    get_product_variant_context,
)


logger = logging.getLogger(__name__)


def home(request):
    currency = get_storefront_currency()
    discovery = prepare_storefront_discovery(request)

    # Featured: include products with OR without variants
    featured_products, on_sale_products = get_home_merchandising_products(currency)
    prepare_product_card_presentations(
        [*featured_products, *on_sale_products],
        language=get_language(),
        currency=currency,
    )
    root_categories, child_categories = get_home_catalog_categories(discovery=discovery)
    application_areas = get_home_application_areas(discovery=discovery)
    brands = get_home_brands(discovery=discovery)
    root_count = len(root_categories)
    category_cards, application_areas, brands = attach_home_merchandising_media(
        (*root_categories, *child_categories), application_areas, brands,
    )
    root_categories, child_categories = category_cards[:root_count], category_cards[root_count:]
    configured_hero = get_homepage_hero()
    editorial_article = get_homepage_editorial_article()
    hero_product = next(
        (
            product
            for product in [*featured_products, *on_sale_products]
            if product.image
        ),
        None,
    )

    return render(request, "products/home.html", {
        "featured_products": featured_products,
        "on_sale_products": on_sale_products,
        "root_categories": root_categories,
        "child_categories": child_categories,
        "application_areas": application_areas,
        "brands": brands,
        "editorial_article": editorial_article,
        "show_offers": len(on_sale_products) >= 4,
        "storefront_currency": currency,
        "hero_image_url": (
            configured_hero.image_url
            if configured_hero
            else hero_product.image.url if hero_product else ""
        ),
        "hero_image_alt": (
            (
                configured_hero.image_alt_fa or configured_hero.image_alt
                if get_language().startswith("fa")
                else configured_hero.image_alt or configured_hero.image_alt_fa
            )
            if configured_hero
            else get_localized_value(hero_product, "name", language=get_language())
            if hero_product
            else ""
        ),
    })


def product_list(request, slug=None):
    currency = get_storefront_currency()
    # ------------------------------------------------------------------
    # Base queryset
    # ------------------------------------------------------------------
    category = None
    if slug:
        category = get_object_or_404(Category, slug=slug)

    query = request.GET.get("q", "").strip()

    # ------------------------------------------------------------------
    # Filters (Updated to handle both singular and plural)
    # ------------------------------------------------------------------
    selected_categories = selected_category_slugs(request)
    selected_brands = request.GET.getlist("brands") or request.GET.getlist("brand")
    selected_designs = request.GET.getlist("designs") or request.GET.getlist("design")
    selected_usages = request.GET.getlist("usages") or request.GET.getlist("usage")
    selected_materials = request.GET.getlist("materials") or request.GET.getlist("material")
    selected_finishes = request.GET.getlist("finishes") or request.GET.getlist("finish")
    selected_colors = request.GET.getlist("colors") or request.GET.getlist("color")
    selected_sizes = request.GET.getlist("sizes") or request.GET.getlist("size")

    min_price = request.GET.get("min_price", "").strip()
    max_price = request.GET.get("max_price", "").strip()

    on_sale = request.GET.get("on_sale")
    in_stock = request.GET.get("in_stock")

    sort_values = request.GET.getlist("sort")
    sort = sort_values[-1] if sort_values else ("relevance" if query else "newest")

    generic_result = None
    try:
        generic_result = build_generic_catalog(
            request, currency, category=category, language=get_language(),
        )
        products = generic_result.products
        variant_qs = generic_result.matching_variants
        variant_filter_scope = generic_result.variant_scoped
        filter_source = "generic"
    except CatalogFilterIntegrityError as exc:
        logger.error(
            "generic_catalog_filter_fallback category_id=%s cause=%s",
            category.pk if category else None, exc,
        )
        has_non_tile = Product.objects.filter(
            is_active=True, product_type__isnull=False,
        ).exclude(product_type__code="tile").exists()
        if has_non_tile:
            # Non-Tile has no truthful legacy representation: fail closed.
            products = get_filtered_products(currency=currency)[0].none()
            variant_qs = ProductVariant.objects.none()
            variant_filter_scope = False
            filter_source = "unavailable"
        else:
            variant_filter_scope = has_active_variant_filters(
                selected_finishes=selected_finishes, selected_colors=selected_colors,
                selected_sizes=selected_sizes, min_price=min_price, max_price=max_price,
                on_sale=on_sale, in_stock=in_stock,
            )
            products, variant_qs = get_filtered_products(
                category=category, query=query, selected_categories=selected_categories,
                selected_brands=selected_brands, selected_designs=selected_designs,
                selected_usages=selected_usages, selected_materials=selected_materials,
                selected_finishes=selected_finishes, selected_colors=selected_colors,
                selected_sizes=selected_sizes, min_price=min_price, max_price=max_price,
                on_sale=on_sale, in_stock=in_stock, currency=currency,
            )
            filter_source = "legacy"

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------
    if sort == "relevance" and filter_source != "generic":
        sort = "newest"
    products = get_sorted_products(
        products, sort, use_catalog_variant_prices=variant_filter_scope
    )
    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    paginator = Paginator(products, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    # We need to determine which variant ID to send the user to
    attach_target_variant_ids(
        page_obj, variant_qs, currency,
        use_catalog_variant_prices=variant_filter_scope,
    )
    if generic_result and query and not variant_filter_scope:
        for product in page_obj.object_list:
            search_target = getattr(product, "search_target_variant_id", None)
            if search_target:
                product.target_variant_id = search_target
    prepare_product_card_presentations(
        page_obj.object_list,
        language=get_language(),
        currency=currency,
    )
    # ------------------------------------------------------------------
    # Sidebar filter options
    # ------------------------------------------------------------------
    if generic_result:
        filter_data = {"filter_options": {}, "max_price": None}
        filter_state = build_generic_filter_state(
            request, generic_result, currency, category=category, query=query,
        )
        filter_facets = generic_result.facets
        selected_brands = generic_result.selected_brands
        selected_product_types = generic_result.selected_product_types
    elif filter_source == "legacy":
        filter_data = get_product_filter_data(currency)
        filter_state = build_catalog_filter_state(
            request, category=category, query=query, currency=currency,
            filter_options={
                **filter_data["filter_options"],
                "categories": Category.objects.filter(is_active=True).order_by("sort_order", "name"),
            },
            selected_filters={
                "categories": selected_categories, "brands": selected_brands,
                "designs": selected_designs, "usages": selected_usages,
                "materials": selected_materials, "finishes": selected_finishes,
                "colors": selected_colors, "sizes": selected_sizes,
            },
            min_price=min_price, max_price=max_price,
            on_sale=on_sale, in_stock=in_stock,
        )
        filter_facets = ()
        selected_product_types = ()
    else:
        filter_data = {"filter_options": {}, "max_price": None}
        filter_state = {
            "active_filter_chips": [], "has_active_criteria": False,
            "active_filter_count": 0,
            "clear_all_url": reverse("products:product_list"),
        }
        filter_facets = ()
        selected_product_types = ()
    # ------------------------------------------------------------------
    # Query string (used by pagination & sorting)
    # ------------------------------------------------------------------
    params = request.GET.copy()
    params.pop("page", None)
    query_string = params.urlencode()

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------
    categories = list(Category.objects.filter(is_active=True).order_by("sort_order", "name"))
    context = {
            "page_obj": page_obj,
            "category": category,
            "categories": categories,
            "category_selector_items": build_category_selector_items(
                request,
                get_category_selector_categories(categories),
                path_category=category,
            ),
            "query": query,
            "query_string": query_string,
            "filter_options": filter_data["filter_options"],
            "filter_facets": filter_facets,
            "brand_options": generic_result.brand_options if generic_result else (),
            "product_type_options": generic_result.product_type_options if generic_result else (),
            "filter_source": filter_source,
            "max_price": filter_data["max_price"],
            "storefront_currency": currency,
            **filter_state,

            "current_filters": {
                "sort": sort,
                "min_price": min_price,
                "max_price": max_price,
                "on_sale": on_sale or "",
                "in_stock": in_stock or "",
            },

            "selected_categories": selected_categories,
            "selected_brands": selected_brands,
            "selected_product_types": selected_product_types,
            "selected_designs": selected_designs,
            "selected_usages": selected_usages,
            "selected_materials": selected_materials,
            "selected_finishes": selected_finishes,
            "selected_colors": selected_colors,
            "selected_sizes": selected_sizes,
            "breadcrumbs": [
                {
                    "label": _("Products"),
                    "url": None,
                }
            ]
    }
    response = render(
        request,
        "products/components/catalog_workspace.html" if is_htmx(request) else "products/product_list.html",
        context,
    )
    # Catalog URL state is the history authority. Prevent browser BFCache from
    # reviving an old, transient drawer/filter DOM after a Product Detail visit.
    patch_cache_control(response, no_store=True)
    return response

def product_detail(request, slug):
    currency = get_storefront_currency()
    product = get_product_by_slug(slug, currency)
    reviews = get_product_review_queryset(
        product,
        request.user,
    )
    comment_form = ReviewCommentForm()

    if not request.session.session_key:
        request.session.create()
    ProductView.objects.create(
        product=product,
        user=request.user if request.user.is_authenticated else None,
        session_key=request.session.session_key or "",
    )

    # --- Related products (shared categories) ---
    related_products = list(get_related_products(product, currency))

    # --- Wishlist status ---
    is_wishlisted = is_product_wishlisted(
        request.user,
        product,
    )
    # --- Review ---
    review_context = get_review_context(
        user=request.user,
        product=product,
        edit_review_id=request.GET.get("edit"),
    )
    # --- Variant context ---
    variant_context = get_product_variant_context(
        product,
        request,
        currency,
    )
    selected_variant = next(
        (
            variant
            for variant in variant_context["variants"]
            if variant.pk == variant_context["preselected_variant_id"]
        ),
        None,
    )
    product_presentation = compose_product_detail_presentation(
        product,
        selected_variant=selected_variant,
        detail=variant_context.get("detail_contract"),
        language=get_language(),
    )
    selected_variant_data = next(
        (
            row
            for row in variant_context["variants_data"]
            if row["id"] == variant_context["preselected_variant_id"]
        ),
        None,
    )
    initial_gallery_images = (
        list(selected_variant_data.get("images", ()))
        if selected_variant_data
        else []
    )
    selected_configuration_values = (
        [
            item["display_value"]
            for item in selected_variant_data.get("detail_specifications", ())
            if item.get("is_variant_dimension") and item.get("display_value")
        ]
        if selected_variant_data
        else []
    )
    prepare_product_card_presentations(
        related_products,
        language=get_language(),
        currency=currency,
    )

    return render(request, "products/product_detail.html", {
        "product": product,
        "product_presentation": product_presentation,
        "variants": variant_context["variants"],
        "variants_data": variant_context["variants_data"],
        "variant_groups": variant_context["variant_groups"],
        "selector_source": variant_context["selector_source"],
        "best_discount": variant_context["best_discount"],
        "min_variant_price": variant_context["min_variant_price"],
        "max_variant_original": variant_context["max_variant_original"],
        "initial_price": variant_context["initial_price"],
        "initial_discount_percent": variant_context["initial_discount_percent"],
        "initial_price_unit": variant_context["initial_price_unit"],
        "initial_selling_unit": variant_context["initial_selling_unit"],
        "storefront_currency": currency,
        "is_wishlisted": is_wishlisted,
        "reviews": reviews,
        "editing_review": review_context["editing_review"],
        "form": review_context["form"],
        "comment_form": comment_form,
        "user_has_purchased": review_context["user_has_purchased"],
        "user_has_review": review_context["user_has_review"],
        "pending_review": review_context["pending_review"],
        "pending_comments": review_context["pending_comments"],
        "pending_reply_parents": review_context["pending_reply_parents"],
        "related_products": related_products,
        "preselected_variant_id": variant_context["preselected_variant_id"],
        "preselected": variant_context["preselected"],
        "selected_variant": selected_variant,
        "selected_variant_data": selected_variant_data,
        "initial_gallery_images": initial_gallery_images,
        "selected_configuration_values": selected_configuration_values,
        "breadcrumbs": [
            {
                    "label": _("Products"),
                "url": reverse("products:product_list"),
            },
            {
                "label": get_localized_value(product, "name"),
                "url": None,
            },
        ]
    })


def search_suggestions(request):
    query = request.GET.get("q", "").strip()
    currency = get_storefront_currency()
    return JsonResponse(autocomplete(query, currency))
