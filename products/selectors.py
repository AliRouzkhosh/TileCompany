"""
products/selectors.py

Read-only database queries for the Products app.

Rules:
- No business logic.
- No object creation/update/delete.
- No messages.
- No redirects.
- No request handling.
- Return QuerySets or domain data only.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from types import SimpleNamespace

from django.db.models import (
    Avg,
    BooleanField,
    Case,
    Count,
    DecimalField,
    ExpressionWrapper,
    Exists,
    F,
    FloatField,
    IntegerField,
    Max,
    Min,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.core.files.storage import default_storage

from .models import (
    Brand,
    Category,
    Color,
    Design,
    Finish,
    HomepageHero,
    HomepageEditorialSelection,
    HomepageMerchandisingMedia,
    Material,
    Product,
    ProductVariant,
    ProductView,
    Size,
    Usage,
    Wishlist,
    VariantPrice,
)
from .presentation import get_localized_value
from .currency import Currency
from .represented_catalog import (
    represented_application_areas, represented_brands, represented_category_hierarchy,
    represented_navigation_categories, shop_filter_url,
)
from django.db.models.functions import Coalesce

from orders.models import OrderItem
from reviews.models import Review, ReviewComment
from django.db.models import Sum as DSum
from .search.engine import search_products
from django.shortcuts import get_object_or_404
from django.utils import timezone
from reviews.models import Review, ReviewComment
from reviews.forms import ReviewForm, ReviewCommentForm


@dataclass(frozen=True, slots=True)
class ProductPriceRange:
    """Derived sell-price bounds for one product in one explicit currency."""

    minimum: Decimal | None
    maximum: Decimal | None

    @property
    def is_priced(self):
        return self.minimum is not None


@dataclass(frozen=True, slots=True)
class HomepageCategoryDTO:
    """Bounded, query-free presentation data for homepage discovery cards."""

    id: int
    slug: str
    name: str
    name_fa: str
    product_count: int
    image_url: str
    url: str = ""
    image_alt: str = ""


@dataclass(frozen=True, slots=True)
class HomepageHeroDTO:
    """Query-free operator-managed hero presentation data."""

    image_url: str
    image_alt: str
    image_alt_fa: str


def _currency_value(currency):
    """Validate and normalize a supported currency without language inference."""

    return Currency(currency).value


def get_variant_price(variant, currency):
    """Return the exact requested currency price, or ``None`` when absent."""

    currency = _currency_value(currency)
    prefetched_prices = getattr(variant, "currency_prices", None)
    if prefetched_prices is not None:
        return next(
            (price for price in prefetched_prices if price.currency == currency),
            None,
        )

    return variant.prices.filter(currency=currency).first()


def prefetch_variant_prices_for_currency(queryset, currency, *, to_attr="currency_prices"):
    """Prefetch one requested currency price for a variant queryset."""

    currency = _currency_value(currency)
    return queryset.prefetch_related(
        Prefetch(
            "prices",
            queryset=VariantPrice.objects.filter(currency=currency),
            to_attr=to_attr,
        )
    )


def get_product_price_range(product, currency, *, active_only=True):
    """Return derived min/max active-variant prices with no currency fallback."""

    currency = _currency_value(currency)
    prices = VariantPrice.objects.filter(
        variant__product=product,
        currency=currency,
    )
    if active_only:
        prices = prices.filter(
            variant__is_active=True,
            variant__product__is_active=True,
        )

    bounds = prices.aggregate(minimum=Min("amount"), maximum=Max("amount"))
    return ProductPriceRange(**bounds)


def annotate_product_price_range(queryset, currency, *, active_only=True):
    """Annotate product querysets efficiently for a requested currency."""

    currency = _currency_value(currency)
    price_filter = Q(variants__prices__currency=currency)
    if active_only:
        price_filter &= Q(variants__is_active=True, is_active=True)

    return queryset.annotate(
        requested_currency_min_price=Min(
            "variants__prices__amount", filter=price_filter
        ),
        requested_currency_max_price=Max(
            "variants__prices__amount", filter=price_filter
        ),
    )


def annotate_product_review_summary(queryset):
    """Annotate public rating data from approved reviews only.

    The review aggregates deliberately live in correlated subqueries.  Catalog
    products already join variants and prices for stock and price annotations;
    adding reviews to that outer query would multiply those rows and corrupt
    both product and review aggregates.
    """

    approved_reviews = (
        Review.objects.filter(product_id=OuterRef("pk"), is_approved=True)
        .order_by()
        .values("product_id")
    )
    return queryset.annotate(
        avg_rating=Subquery(
            approved_reviews.annotate(value=Avg("rating")).values("value")[:1],
            output_field=FloatField(),
        ),
        review_count=Coalesce(
            Subquery(
                approved_reviews.annotate(value=Count("pk")).values("value")[:1],
                output_field=IntegerField(),
            ),
            Value(0),
        ),
    )


# ==========================================================
# Product QuerySets
# ==========================================================

def _storefront_variant_queryset(currency):
    """Active variants with only the requested currency price prefetched."""

    return prefetch_variant_prices_for_currency(
        ProductVariant.objects.filter(is_active=True)
        .select_related("color", "size", "finish")
        .order_by("sort_order", "id"),
        currency,
    )


def _get_catalog_queryset(currency):
    """
    Base queryset for active catalog products with dashboard/storefront
    annotations.
    """

    currency = _currency_value(currency)
    active_variants = ProductVariant.objects.filter(product=OuterRef("pk"), is_active=True)
    active_variant_stock = (
        active_variants.order_by()
        .values("product_id")
        .annotate(value=Sum("stock"))
        .values("value")[:1]
    )
    sale_prices = VariantPrice.objects.filter(
        variant__product=OuterRef("pk"),
        variant__is_active=True,
        currency=currency,
        compare_at_amount__gt=F("amount"),
    )
    sale_discount_percent = sale_prices.annotate(
        discount_percent=ExpressionWrapper(
            (F("compare_at_amount") - F("amount")) * Value(100) / F("compare_at_amount"),
            output_field=DecimalField(max_digits=7, decimal_places=2),
        )
    ).order_by("-discount_percent").values("discount_percent")[:1]

    queryset = (
        Product.objects.filter(is_active=True)
        .select_related("brand")
        .prefetch_related(
            "categories",
            "designs",
            "materials",
            "usages",
            Prefetch("variants", queryset=_storefront_variant_queryset(currency)),
        )
        .annotate(
            has_active_variants=Exists(active_variants),

            total_variant_stock=Coalesce(
                Subquery(active_variant_stock, output_field=IntegerField()),
                Value(0),
            ),

            has_requested_currency_sale=Exists(sale_prices),
            requested_currency_sale_percent=Subquery(sale_discount_percent),
        )
    )
    # Keep range annotations on the product queryset, never in templates.
    return annotate_product_review_summary(
        annotate_product_price_range(queryset, currency)
    )


def get_product_by_slug(slug, currency):
    """
    Return an active product by its slug.
    """

    return get_object_or_404(
        annotate_product_review_summary(
            annotate_product_price_range(
                Product.objects.select_related("brand", "product_type").prefetch_related(
            "categories",
            "designs",
            "materials",
            "usages",
            "gallery",
            ),
            currency,
            )
        ),
        slug=slug,
        is_active=True,
    )

def get_product_review_queryset(product, user):
    """
    Return approved product reviews with their comments and replies.

    Unapproved comments are only visible to their author.
    """

    visible_comment_filter = Q(is_approved=True)
    if user.is_authenticated:
        visible_comment_filter |= Q(user=user, is_approved=False)
    visible_comment_count_filter = Q(comments__is_approved=True)
    if user.is_authenticated:
        visible_comment_count_filter |= Q(comments__user=user, comments__is_approved=False)

    return (
        product.reviews
        .filter(is_approved=True)
        .select_related("user")
        # The discussion renders visible top-level comments and their visible
        # nested replies.  Count that same viewer-visible collection so the
        # header never leaks another user's pending work or omits top-level
        # discussion entries.
        .annotate(reply_count=Count("comments", filter=visible_comment_count_filter))
        .prefetch_related(
            Prefetch(
                "comments",
                queryset=(
                    ReviewComment.objects
                    .filter(parent__isnull=True)
                    .filter(
                        visible_comment_filter
                    )
                    .select_related("user")
                    .prefetch_related(
                        Prefetch(
                            "replies",
                            queryset=(
                                ReviewComment.objects
                                .filter(
                                    visible_comment_filter
                                )
                                .select_related("user")
                            ),
                        )
                    )
                ),
            )
        )
    )

def get_related_products(product, currency, limit=4):
    """
    Return active products sharing at least one category.

    The current product is excluded.
    """

    currency = _currency_value(currency)
    related_sale_prices = VariantPrice.objects.filter(
        variant__product=OuterRef("pk"),
        variant__is_active=True,
        currency=currency,
        compare_at_amount__gt=F("amount"),
    )
    related_sale_discount_percent = related_sale_prices.annotate(
        discount_percent=ExpressionWrapper(
            (F("compare_at_amount") - F("amount")) * Value(100) / F("compare_at_amount"),
            output_field=DecimalField(max_digits=7, decimal_places=2),
        )
    ).order_by("-discount_percent").values("discount_percent")[:1]
    queryset = (
        Product.objects.filter(
            is_active=True,
            categories__in=product.categories.all(),
        )
        .exclude(pk=product.pk)
        .select_related("brand")
        .annotate(
            has_requested_currency_sale=Exists(
                related_sale_prices
            ),
            requested_currency_sale_percent=Subquery(related_sale_discount_percent),
        )
        .prefetch_related(
            "categories",
            "designs",
            "materials",
            "usages",
            Prefetch(
                "variants",
                queryset=(
                    _storefront_variant_queryset(currency)
                ),
            ),
        )
        .distinct()
        .order_by("-updated_at")
    )
    return annotate_product_review_summary(
        annotate_product_price_range(queryset, currency)
    )[:limit]

def get_recently_viewed_products(
    *,
    user,
    session_key,
    current_product,
    currency,
    limit=4,
):
    """
    Return the most recently viewed active products, excluding
    the current product and removing duplicates.
    """

    recent_qs = (
        ProductView.objects
        .filter(viewed_at__isnull=False)
        .order_by("-viewed_at")
    )

    if user.is_authenticated:
        recent_qs = recent_qs.filter(user=user)
    else:
        recent_qs = recent_qs.filter(session_key=session_key)

    recent_qs = (
        recent_qs
        .exclude(product=current_product)
        .values_list("product_id", flat=True)[:10]
    )

    ordered_ids = list(dict.fromkeys(recent_qs))
    products = _get_catalog_queryset(currency).filter(pk__in=ordered_ids)
    product_map = {product.pk: product for product in products}
    return [product_map[pk] for pk in ordered_ids if pk in product_map][:limit]

def is_product_wishlisted(user, product):
    """
    Return True if the authenticated user has wishlisted the product.
    """

    if not user.is_authenticated:
        return False

    return Wishlist.objects.filter(
        user=user,
        product=product,
    ).exists()


def get_wishlist_items(*, user, currency):
    """Return saved Products with current public catalog presentation facts.

    Wishlist ownership is intentionally independent from publication.  A saved
    Product remains visible when it is later unpublished, but has no public
    destination or current price claim in that state.
    """

    currency = _currency_value(currency)
    active_variants = ProductVariant.objects.filter(
        product=OuterRef("pk"), is_active=True
    )
    stocked_variants = active_variants.filter(stock__gt=0)
    sale_prices = VariantPrice.objects.filter(
        variant__product=OuterRef("pk"),
        variant__is_active=True,
        currency=currency,
        compare_at_amount__gt=F("amount"),
    )
    sale_discount_percent = sale_prices.annotate(
        discount_percent=ExpressionWrapper(
            (F("compare_at_amount") - F("amount")) * Value(100) / F("compare_at_amount"),
            output_field=DecimalField(max_digits=7, decimal_places=2),
        )
    ).order_by("-discount_percent").values("discount_percent")[:1]

    products = (
        Product.objects.filter(wishlisted_by__user=user)
        .select_related("brand", "product_type")
        .prefetch_related(
            "categories",
            "designs",
            "materials",
            "usages",
            Prefetch("variants", queryset=_storefront_variant_queryset(currency)),
        )
        .annotate(
            wishlist_added_at=Max("wishlisted_by__added_at"),
            has_active_variants=Exists(active_variants),
            is_wishlist_available=Case(
                When(is_active=True, then=Exists(stocked_variants)),
                default=Value(False),
                output_field=BooleanField(),
            ),
            has_requested_currency_sale=Exists(sale_prices),
            requested_currency_sale_percent=Subquery(sale_discount_percent),
        )
        .order_by("-wishlist_added_at")
    )
    # Price facts are current-catalog facts: unpublished Products deliberately
    # have no price presentation, even if historical VariantPrice rows remain.
    return annotate_product_price_range(products, currency)

def get_review_context(
    *,
    user,
    product,
    edit_review_id=None,
):
    """
    Build the review-related context for the product detail page.
    """

    context = {
        "form": None,
        "editing_review": None,
        "user_has_purchased": False,
        "user_has_review": False,
        "pending_review": False,
        "pending_comments": set(),
        "pending_reply_parents": set(),
    }

    if not user.is_authenticated:
        return context

    context["user_has_purchased"] = OrderItem.objects.filter(
        order__user=user,
        order__status="delivered",
        product=product,
    ).exists()

    context["user_review"] = Review.objects.filter(
        user=user,
        product=product,
    ).first()
    context["user_has_review"] = context["user_review"] is not None

    context["pending_review"] = Review.objects.filter(
        user=user,
        product=product,
        is_approved=False,
    ).exists()

    context["pending_comments"] = set(
        ReviewComment.objects.filter(
            user=user,
            parent__isnull=True,
            is_approved=False,
        ).values_list("review_id", flat=True)
    )

    context["pending_reply_parents"] = set(
        ReviewComment.objects.filter(
            user=user,
            parent__isnull=False,
            is_approved=False,
        ).values_list("parent_id", flat=True)
    )

    # Edit mode
    if edit_review_id:
        editing_review = (
            Review.objects.filter(
                id=edit_review_id,
                user=user,
                product=product,
            )
            .first()
        )

        if editing_review:
            context["editing_review"] = editing_review
            context["form"] = ReviewForm(instance=editing_review)

    # New review
    elif (
        context["user_has_purchased"]
        and not context["user_has_review"]
    ):
        context["form"] = ReviewForm()

    return context

def _get_legacy_product_variant_context(product, request, currency):
    """
    Build all variant-related context for the product detail page.
    """

    currency = _currency_value(currency)
    variants = list(
        prefetch_variant_prices_for_currency(
            product.variants.filter(is_active=True)
            .select_related("color", "size", "finish")
            .prefetch_related("gallery")
            .order_by("sort_order", "id"),
            currency,
        )
    )

    selected_size = request.GET.get("size", "").strip()
    selected_color = request.GET.get("color", "").strip()
    selected_finish = request.GET.get("finish", "").strip()

    preselected_variant = None

    variant_id = request.GET.get("variant")

    if variant_id:
        preselected_variant = next(
            (variant for variant in variants if str(variant.id) == variant_id),
            None,
        )

    else:
        matching_variants = variants
        if selected_size:
            matching_variants = [
                variant for variant in matching_variants
                if variant.size and variant.size.slug == selected_size
            ]
        if selected_color:
            matching_variants = [
                variant for variant in matching_variants
                if variant.color and variant.color.slug == selected_color
            ]
        if selected_finish:
            matching_variants = [
                variant for variant in matching_variants
                if variant.finish and variant.finish.slug == selected_finish
            ]
        preselected_variant = next(
            (variant for variant in matching_variants if get_variant_price(variant, currency)),
            matching_variants[0] if matching_variants else None,
        )

    variants_data = []
    for variant in variants:
        price = get_variant_price(variant, currency)
        discount_percent = 0
        if price and price.compare_at_amount:
            discount_percent = round(
                (1 - (price.amount / price.compare_at_amount)) * 100
            )
        variants_data.append({
            "id": variant.id,
            "size": variant.size.slug if variant.size else "",
            "size_label": get_localized_value(variant.size, "name") if variant.size else "",
            "color": variant.color.slug if variant.color else "",
            "color_label": get_localized_value(variant.color, "name") if variant.color else "",
            "finish": variant.finish.slug if variant.finish else "",
            "finish_label": get_localized_value(variant.finish, "name") if variant.finish else "",
            "amount": str(price.amount) if price else None,
            "compare_at_amount": str(price.compare_at_amount) if price and price.compare_at_amount else None,
            "currency": currency,
            "price_unit": variant.price_unit,
            "price_unit_display": variant.get_price_unit_display(),
            "is_priced": price is not None,
            "discount_percent": discount_percent,
            "stock": variant.stock,
            "sku": variant.sku,
            "images": [image.image.url for image in variant.gallery.all()] or [image.image.url for image in product.gallery.all()] or ([product.image.url] if product.image else []),
            "image": (variant.gallery.first().image.url if variant.gallery.exists() else (product.gallery.first().image.url if product.gallery.exists() else (product.image.url if product.image else ""))),
        })

    best_discount = max(
        (v["discount_percent"] for v in variants_data),
        default=0,
    )

    min_variant_price = min(
        (Decimal(v["amount"]) for v in variants_data if v["amount"] is not None),
        default=None,
    )

    max_variant_original = max(
        (
            Decimal(v["compare_at_amount"])
            for v in variants_data
            if v["compare_at_amount"]
        ),
        default=None,
    )

    variant_options = {
        "sizes": sorted(
            {variant.size for variant in variants if variant.size},
            key=lambda size: (size.length_mm or 0, size.width_mm or 0, size.name),
        ),
        "colors": sorted(
            {variant.color for variant in variants if variant.color},
            key=lambda color: color.name,
        ),
        "finishes": sorted(
            {variant.finish for variant in variants if variant.finish},
            key=lambda finish: finish.name,
        ),
    }

    initial_price = get_variant_price(preselected_variant, currency) if preselected_variant else None
    initial_discount_percent = 0
    if initial_price and initial_price.compare_at_amount and initial_price.compare_at_amount > initial_price.amount:
        initial_discount_percent = round(
            (1 - (initial_price.amount / initial_price.compare_at_amount)) * 100
        )

    return {
        "variants": variants,
        "variants_data": variants_data,
        "variant_options": variant_options,
        "best_discount": best_discount,
        "min_variant_price": min_variant_price,
        "max_variant_original": max_variant_original,
        "initial_price": initial_price,
        "initial_discount_percent": initial_discount_percent,
        "initial_price_unit": (
            preselected_variant.get_price_unit_display()
            if preselected_variant else ""
        ),
        "storefront_currency": currency,
        "preselected_variant_id": (
            preselected_variant.id
            if preselected_variant
            else None
        ),
        "preselected": {
            "size": (
                preselected_variant.size.slug
                if preselected_variant
                and preselected_variant.size
                else selected_size
            ),
            "color": (
                preselected_variant.color.slug
                if preselected_variant
                and preselected_variant.color
                else selected_color
            ),
            "finish": (
                preselected_variant.finish.slug
                if preselected_variant
                and preselected_variant.finish
                else selected_finish
            ),
        },
    }


def get_product_variant_context(product, request, currency):
    """Compose the generic browser selector with an isolated Tile fallback."""
    import logging

    from .catalog_read import CatalogReadIntegrityError
    from .variant_selector import (
        adapt_complete_legacy_selector,
        add_tile_legacy_payload_aliases,
        compose_generic_variant_selector,
    )

    currency = _currency_value(currency)
    variants = list(
        prefetch_variant_prices_for_currency(
            product.variants.filter(is_active=True)
            .prefetch_related("gallery")
            .order_by("sort_order", "id"),
            currency,
        )
    )
    try:
        context = compose_generic_variant_selector(
            product=product, request=request, currency=currency,
            variants=variants, price_getter=get_variant_price,
        )
    except CatalogReadIntegrityError as error:
        is_tile_compat = not product.product_type_id or product.product_type.code == "tile"
        logging.getLogger(__name__).error(
            "generic_variant_selector_fallback product_id=%s sku=%s cause=%s",
            product.pk, product.sku, error,
            extra={
                "event": "generic_variant_selector_fallback",
                "product_id": product.pk, "product_sku": product.sku,
                "catalog_read_cause": str(error),
            },
        )
        if not is_tile_compat:
            return {
                "variants": [], "variants_data": [], "variant_groups": [],
                "selector_source": "unusable", "parameter_source": "integrity_error",
                "preselected": {}, "preselected_variant_id": None,
                "initial_price": None, "initial_discount_percent": 0,
                "initial_price_unit": "", "initial_selling_unit": {}, "best_discount": 0,
                "min_variant_price": None, "max_variant_original": None,
                "storefront_currency": currency,
            }
        return adapt_complete_legacy_selector(
            _get_legacy_product_variant_context(product, request, currency),
            product=product,
        )
    if product.product_type_id and product.product_type.code == "tile":
        add_tile_legacy_payload_aliases(context)
    return context
# ==========================================================
# Home Page
# ==========================================================

def get_featured_products(currency, limit=8):
    return (
        _get_catalog_queryset(currency)
        .filter(is_featured=True)
        .order_by("-updated_at")[:limit]
    )


def get_on_sale_products(currency, limit=8):
    return (
        _get_catalog_queryset(currency)
        .filter(has_requested_currency_sale=True)
        .order_by("-updated_at")[:limit]
    )


def get_home_categories(limit=6):
    """Return active, navigable roots with one batched representative image.

    The result is deterministic and capped because this selector only serves
    the compact homepage discovery rail. Product counts and images include
    active products only; templates never traverse category relationships.
    """

    limit = max(1, min(int(limit), 8))
    representative_image = (
        Product.objects.filter(categories=OuterRef("pk"), is_active=True)
        .exclude(Q(image="") | Q(image__isnull=True))
        .order_by("-is_featured", "-updated_at", "id")
        .values("image")[:1]
    )
    rows = (
        Category.objects.filter(
            is_active=True,
            show_in_navigation=True,
            parent__isnull=True,
        )
        .annotate(
            product_count=Count(
                "categorized_products",
                filter=Q(categorized_products__is_active=True),
                distinct=True,
            ),
            homepage_image_name=Subquery(representative_image),
        )
        .order_by("sort_order", "name", "id")[:limit]
    )
    return tuple(
        HomepageCategoryDTO(
            id=row.id,
            slug=row.slug,
            name=row.name,
            name_fa=row.name_fa,
            product_count=row.product_count,
            image_url=(
                default_storage.url(row.storefront_image.name)
                if row.storefront_image
                else default_storage.url(row.homepage_image_name)
                if row.homepage_image_name
                else ""
            ),
        )
        for row in rows
    )


def get_home_catalog_categories(*, root_limit=2, child_limit=6, discovery=None):
    """Homepage-ready represented roots and children from the shared hierarchy."""
    if discovery is None:
        rows, direct_counts = represented_navigation_categories(limit=60)
    else:
        rows, direct_counts = discovery.categories, discovery.category_counts
    represented, counts, children_by_parent = represented_category_hierarchy(rows, direct_counts)

    def dto(row):
        return HomepageCategoryDTO(
            id=row.id, slug=row.slug, name=row.name, name_fa=row.name_fa,
            product_count=counts.get(row.id, 0),
            image_url=(default_storage.url(row.storefront_image.name) if row.storefront_image else ""),
            url=shop_filter_url(categories=row.slug), image_alt=row.name,
        )

    roots = [dto(row) for row in children_by_parent[None] if row.id in represented][:root_limit]
    children = [
        dto(row)
        for root in children_by_parent[None]
        if root.id in represented
        for row in children_by_parent.get(root.id, ())
        if row.id in represented
    ][:child_limit]
    return tuple(roots), tuple(children)


def get_home_application_areas(limit=6, *, discovery=None):
    areas = (
        discovery.application_areas[:limit]
        if discovery is not None
        else represented_application_areas({
            "definition_code": "demo-application-area", "product_type_code": "ceramic-tile", "limit": limit,
        })
    )
    prepared = []
    for area in areas:
        prepared.append(SimpleNamespace(**area, url=shop_filter_url(
            product_type=area["product_type_code"],
            **{f"attr_{area['definition_code']}": area["code"]},
        )))
    return tuple(prepared)


def get_home_brands(limit=10, *, discovery=None):
    brands = list(discovery.brands[:limit]) if discovery is not None else represented_brands(limit=limit)
    for brand in brands:
        brand.url = shop_filter_url(brand=brand.slug)
    return tuple(brands)


def attach_home_merchandising_media(categories, application_areas, brands):
    """Attach optional discovery imagery in one query without changing canonical data."""
    category_ids = [item.id for item in categories]
    option_ids = [item.id for item in application_areas]
    brand_ids = [item.id for item in brands]
    media_by_target = {}
    for media in HomepageMerchandisingMedia.objects.filter(is_active=True).filter(
        Q(category_id__in=category_ids) | Q(attribute_option_id__in=option_ids) | Q(brand_id__in=brand_ids)
    ).only("category_id", "attribute_option_id", "brand_id", "image", "image_alt", "image_alt_fa"):
        target = ("category", media.category_id) if media.category_id else ("option", media.attribute_option_id) if media.attribute_option_id else ("brand", media.brand_id)
        media_by_target[target] = media

    categories = tuple(
        replace(
            item,
            image_url=(media_by_target[("category", item.id)].image.url if ("category", item.id) in media_by_target and media_by_target[("category", item.id)].image else item.image_url),
            image_alt=(media_by_target[("category", item.id)].image_alt or item.name if ("category", item.id) in media_by_target else item.name),
        )
        for item in categories
    )
    for kind, items in (("option", application_areas), ("brand", brands)):
        for item in items:
            media = media_by_target.get((kind, item.id))
            item.image_url = media.image.url if media and media.image else ""
            item.image_alt = (media.image_alt if media else "") or item.name
            item.image_alt_fa = (media.image_alt_fa if media else "") or item.name_fa
    return categories, application_areas, brands


def get_home_merchandising_products(currency, *, limit=8):
    """Bounded homepage rails: featured first, then non-duplicated genuine offers."""
    featured = list(
        _get_catalog_queryset(currency).filter(is_featured=True)
        .order_by("has_requested_currency_sale", "-updated_at", "id")[:limit]
    )
    offers = list(
        _get_catalog_queryset(currency).filter(has_requested_currency_sale=True)
        .exclude(pk__in=[product.pk for product in featured])
        .order_by(F("requested_currency_sale_percent").desc(nulls_last=True), "-updated_at", "id")[:limit]
    )
    return tuple(featured), tuple(offers)


def get_homepage_hero():
    """Return the enabled authoritative hero using one bounded query."""

    hero = (
        HomepageHero.objects.filter(
            key=HomepageHero.PRIMARY_KEY,
            is_enabled=True,
        )
        .only("image", "image_alt", "image_alt_fa")
        .first()
    )
    if not hero or not hero.image:
        return None
    return HomepageHeroDTO(
        image_url=hero.image.url,
        image_alt=hero.image_alt,
        image_alt_fa=hero.image_alt_fa,
    )


def get_homepage_editorial_article():
    """Return an eligible explicit selection, otherwise preserve automatic selection."""
    from blog.models import ArticleStatus
    from blog.selectors import get_latest_articles

    selection = (
        HomepageEditorialSelection.objects.filter(
            key=HomepageEditorialSelection.PRIMARY_KEY,
            article__status=ArticleStatus.PUBLISHED,
            article__published_at__lte=timezone.now(),
        )
        .select_related("article__author", "article__category")
        .first()
    )
    if selection:
        return selection.article
    latest_articles = list(get_latest_articles(6))
    return next(
        (article for article in latest_articles if article.cover_image),
        latest_articles[0] if latest_articles else None,
    )

# ==========================================================
# Product Listing
# ==========================================================

def get_product_filter_data(currency):
    """
    Returns all sidebar filter options used on the catalog page.
    """

    currency = _currency_value(currency)
    price_base = VariantPrice.objects.filter(
        currency=currency,
        variant__is_active=True,
        variant__product__is_active=True,
    )

    price_range = price_base.aggregate(
        min_price=Min("amount"),
        max_price=Max("amount"),
    )

    return {
        "filter_options": {
            "brands": (
                Brand.objects
                .filter(products__is_active=True)
                .distinct()
                .order_by("name")
            ),

            "designs": (
                Design.objects
                .filter(products__is_active=True)
                .distinct()
                .order_by("name")
            ),

            "usages": (
                Usage.objects
                .filter(products__is_active=True)
                .distinct()
                .order_by("name")
            ),

            "materials": (
                Material.objects
                .filter(products__is_active=True)
                .distinct()
                .order_by("name")
            ),

            "finishes": (
                Finish.objects
                .filter(variants__is_active=True, variants__product__is_active=True)
                .distinct()
                .order_by("name")
            ),

            "colors": (
                Color.objects
                .filter(variants__is_active=True, variants__product__is_active=True)
                .distinct()
                .order_by("name")
            ),

            "sizes": (
                Size.objects
                .filter(variants__is_active=True, variants__product__is_active=True)
                .distinct()
                .order_by("length_mm", "width_mm", "name")
            ),
        },

        "max_price": price_range["max_price"] or 10_000_000,
    }

# ==========================================================
# Product Sorting
# ==========================================================
def _get_product_sales():
    return dict(
        OrderItem.objects.filter(order__is_paid=True)
        .values_list("product_id")
        .annotate(total=DSum("quantity"))
    )
def get_sorted_products(products, sort, *, use_catalog_variant_prices=False):
    """
    Return products sorted according to the selected catalog sort option.
    """

    price_field = (
        "catalog_min_price"
        if use_catalog_variant_prices
        else "requested_currency_min_price"
    )

    if sort == "relevance":
        return products.order_by(F("search_rank").desc(nulls_last=True), "-created_at", "pk")

    if sort == "price_asc":
        return products.order_by(F(price_field).asc(nulls_last=True))

    if sort == "price_desc":
        return products.order_by(F(price_field).desc(nulls_last=True))

    if sort == "rating":
        return products.order_by(
            F("avg_rating").desc(nulls_last=True),
            F("review_count").desc(),
            "pk",
        )

    if sort == "best_selling":
        sales = _get_product_sales()

        return sorted(
            products,
            key=lambda p: sales.get(p.id, 0),
            reverse=True,
        )

    return products.order_by("-created_at")
# ==========================================================
# Product Listing
# ==========================================================
def _apply_search(products, query):
    """
    Apply catalog search to the product queryset.
    """
    if not query:
        return products

    matched = search_products(query)
    return products.filter(id__in=matched.values("id")).distinct()

def _apply_product_filters(
    products,
    *,
    category=None,
    selected_categories=None,
    selected_brands=None,
    selected_designs=None,
    selected_usages=None,
    selected_materials=None,
):
    """
    Apply product-level filters.
    """

    if category:
        products = products.filter(categories=category).distinct()

    if selected_categories:
        products = products.filter(
            categories__slug__in=selected_categories
        ).distinct()

    if selected_brands:
        products = products.filter(
            brand__slug__in=selected_brands
        ).distinct()

    if selected_designs:
        products = products.filter(
            designs__slug__in=selected_designs
        ).distinct()

    if selected_usages:
        products = products.filter(usages__slug__in=selected_usages).distinct()

    if selected_materials:
        products = products.filter(materials__slug__in=selected_materials).distinct()

    return products

def _apply_variant_filters(
    products,
    *,
    selected_finishes=None,
    selected_colors=None,
    selected_sizes=None,
    min_price=None,
    max_price=None,
    on_sale=None,
    in_stock=None,
    currency=None,
):
    """
    Apply variant-based filters.
    """

    currency = _currency_value(currency)
    variant_qs = prefetch_variant_prices_for_currency(ProductVariant.objects.filter(
        is_active=True,
        product__is_active=True,
    ).select_related("color", "size", "finish"), currency)

    if selected_finishes:
        variant_qs = variant_qs.filter(finish__slug__in=selected_finishes)

    if selected_colors:
        variant_qs = variant_qs.filter(color__slug__in=selected_colors)

    if selected_sizes:
        variant_qs = variant_qs.filter(size__slug__in=selected_sizes)

    if min_price:
        try:
            variant_qs = variant_qs.filter(
                prices__currency=currency,
                prices__amount__gte=Decimal(min_price),
            )
        except (ValueError, ArithmeticError):
            pass

    if max_price:
        try:
            variant_qs = variant_qs.filter(
                prices__currency=currency,
                prices__amount__lte=Decimal(max_price),
            )
        except (ValueError, ArithmeticError):
            pass

    if on_sale == "1":
        variant_qs = variant_qs.filter(
            prices__currency=currency,
            prices__compare_at_amount__gt=F("prices__amount"),
        )

    if in_stock == "1":
        variant_qs = variant_qs.filter(stock__gt=0)

    return (
        products.filter(variants__in=variant_qs).distinct(),
        variant_qs,
    )


def has_active_variant_filters(
    *,
    selected_finishes=None,
    selected_colors=None,
    selected_sizes=None,
    min_price=None,
    max_price=None,
    on_sale=None,
    in_stock=None,
):
    """Return whether catalog presentation must be limited to matching variants."""
    if any((selected_finishes, selected_colors, selected_sizes)):
        return True
    for value in (min_price, max_price):
        if value:
            try:
                Decimal(value)
                return True
            except (ValueError, ArithmeticError):
                pass
    return on_sale == "1" or in_stock == "1"


def annotate_catalog_variant_presentation(
    products, currency, *, selected_finishes=None, selected_colors=None,
    selected_sizes=None, min_price=None, max_price=None, on_sale=None, in_stock=None,
):
    """Attach filter-scoped card data from the already matching variant set.

    Global requested-currency annotations remain untouched for non-catalog
    contexts.  These correlated subqueries execute before ordering/pagination.
    """
    currency = _currency_value(currency)
    variant_filter = Q(variants__is_active=True)
    if selected_finishes:
        variant_filter &= Q(variants__finish__slug__in=selected_finishes)
    if selected_colors:
        variant_filter &= Q(variants__color__slug__in=selected_colors)
    if selected_sizes:
        variant_filter &= Q(variants__size__slug__in=selected_sizes)
    if in_stock == "1":
        variant_filter &= Q(variants__stock__gt=0)

    priced_filter = variant_filter & Q(variants__prices__currency=currency)
    for value, lookup in ((min_price, "gte"), (max_price, "lte")):
        if value:
            try:
                priced_filter &= Q(**{f"variants__prices__amount__{lookup}": Decimal(value)})
            except (ValueError, ArithmeticError):
                pass
    if on_sale == "1":
        priced_filter &= Q(variants__prices__compare_at_amount__gt=F("variants__prices__amount"))

    target_variants = ProductVariant.objects.filter(
        product_id=OuterRef("pk"), is_active=True
    )
    if selected_finishes:
        target_variants = target_variants.filter(finish__slug__in=selected_finishes)
    if selected_colors:
        target_variants = target_variants.filter(color__slug__in=selected_colors)
    if selected_sizes:
        target_variants = target_variants.filter(size__slug__in=selected_sizes)
    if in_stock == "1":
        target_variants = target_variants.filter(stock__gt=0)
    target_variants = target_variants.annotate(
        catalog_price=Subquery(
            VariantPrice.objects.filter(variant_id=OuterRef("pk"), currency=currency)
            .values("amount")[:1]
        ),
        catalog_compare_at=Subquery(
            VariantPrice.objects.filter(variant_id=OuterRef("pk"), currency=currency)
            .values("compare_at_amount")[:1]
        ),
    ).filter(catalog_price__isnull=False)
    if min_price:
        try:
            target_variants = target_variants.filter(catalog_price__gte=Decimal(min_price))
        except (ValueError, ArithmeticError):
            pass
    if max_price:
        try:
            target_variants = target_variants.filter(catalog_price__lte=Decimal(max_price))
        except (ValueError, ArithmeticError):
            pass
    if on_sale == "1":
        target_variants = target_variants.filter(catalog_compare_at__gt=F("catalog_price"))
    matching_count_variants = ProductVariant.objects.filter(
        product_id=OuterRef("pk"), is_active=True
    )
    if selected_finishes:
        matching_count_variants = matching_count_variants.filter(finish__slug__in=selected_finishes)
    if selected_colors:
        matching_count_variants = matching_count_variants.filter(color__slug__in=selected_colors)
    if selected_sizes:
        matching_count_variants = matching_count_variants.filter(size__slug__in=selected_sizes)
    if in_stock == "1":
        matching_count_variants = matching_count_variants.filter(stock__gt=0)
    if min_price:
        try:
            matching_count_variants = matching_count_variants.filter(
                prices__currency=currency, prices__amount__gte=Decimal(min_price)
            )
        except (ValueError, ArithmeticError):
            pass
    if max_price:
        try:
            matching_count_variants = matching_count_variants.filter(
                prices__currency=currency, prices__amount__lte=Decimal(max_price)
            )
        except (ValueError, ArithmeticError):
            pass
    if on_sale == "1":
        matching_count_variants = matching_count_variants.filter(
            prices__currency=currency,
            prices__compare_at_amount__gt=F("prices__amount"),
        )
    target_price = target_variants.annotate(
        discount_percent=Case(
            When(
                catalog_compare_at__gt=F("catalog_price"),
                then=ExpressionWrapper(
                    (F("catalog_compare_at") - F("catalog_price")) * Value(100) / F("catalog_compare_at"),
                    output_field=DecimalField(max_digits=7, decimal_places=2),
                ),
            ),
            default=Value(None, output_field=DecimalField(max_digits=7, decimal_places=2)),
            output_field=DecimalField(max_digits=7, decimal_places=2),
        )
    ).order_by("catalog_price", "sort_order", "id")

    return products.annotate(
        catalog_pricing_is_scoped=Value(True),
        catalog_matching_variant_count=Subquery(
            matching_count_variants.order_by().values("product_id")
            .annotate(count=Count("pk")).values("count")[:1]
        ),
        catalog_min_price=Min("variants__prices__amount", filter=priced_filter),
        catalog_max_price=Max("variants__prices__amount", filter=priced_filter),
        catalog_target_variant_id=Subquery(target_price.values("id")[:1]),
        catalog_sale_percent=Subquery(target_price.values("discount_percent")[:1]),
    )
def get_filtered_products(
    *,
    category=None,
    query="",
    selected_categories=None,
    selected_brands=None,
    selected_designs=None,
    selected_usages=None,
    selected_materials=None,
    selected_finishes=None,
    selected_colors=None,
    selected_sizes=None,
    min_price=None,
    max_price=None,
    on_sale=None,
    in_stock=None,
    currency=None,
):
    """
    Return the filtered catalog queryset and matching variants.
    """

    products = _get_catalog_queryset(currency)

    products = _apply_search(
        products,
        query,
    )

    products = _apply_product_filters(
        products,
        category=category,
        selected_categories=selected_categories,
        selected_brands=selected_brands,
        selected_designs=selected_designs,
        selected_usages=selected_usages,
        selected_materials=selected_materials,
    )

    products, variant_qs = _apply_variant_filters(
        products,
        selected_finishes=selected_finishes,
        selected_colors=selected_colors,
        selected_sizes=selected_sizes,
        min_price=min_price,
        max_price=max_price,
        on_sale=on_sale,
        in_stock=in_stock,
        currency=currency,
    )
    if has_active_variant_filters(
        selected_finishes=selected_finishes,
        selected_colors=selected_colors,
        selected_sizes=selected_sizes,
        min_price=min_price,
        max_price=max_price,
        on_sale=on_sale,
        in_stock=in_stock,
    ):
        products = annotate_catalog_variant_presentation(
            products, currency,
            selected_finishes=selected_finishes, selected_colors=selected_colors,
            selected_sizes=selected_sizes, min_price=min_price, max_price=max_price,
            on_sale=on_sale, in_stock=in_stock,
        )
    return products, variant_qs

def attach_target_variant_ids(products, variant_qs, currency, *, use_catalog_variant_prices=False):
    """
    Attach the most appropriate variant ID to each product for
    product detail navigation.

    Preference order:
    1. Matching filtered variant
    2. Highest discount
    3. Lowest price
    4. First active variant
    """

    currency = _currency_value(currency)
    if use_catalog_variant_prices:
        for product in products:
            product.target_variant_id = product.catalog_target_variant_id
        return products

    matching_by_product = {}
    for variant in variant_qs:
        matching_by_product.setdefault(variant.product_id, []).append(variant)

    for product in products:
        candidates = matching_by_product.get(product.id, list(product.variants.all()))
        candidates = [
            variant for variant in candidates
            if get_variant_price(variant, currency) is not None
        ]
        candidates.sort(
            key=lambda variant: (
                not bool(get_variant_price(variant, currency).compare_at_amount),
                get_variant_price(variant, currency).amount,
            )
        )
        product.target_variant_id = candidates[0].id if candidates else None

    return products
