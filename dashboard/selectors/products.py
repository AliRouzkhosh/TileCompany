############ dashboard/selectors/products.py ################

from django.db.models import Count, Min, Max, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404


from products.currency import Currency
from products.models import Brand, Product, ProductVariant, Category
from reviews.models import Review
from orders.models import OrderItem

from dataclasses import dataclass


@dataclass(slots=True)
class ProductStatistics:
    gallery_images: int
    variants: int
    active_variants: int
    total_stock: int


@dataclass(slots=True)
class InventorySummary:
    total_stock: int
    active_variants: int
    in_stock_variants: int
    low_stock_variants: int
    out_of_stock_variants: int

@dataclass(slots=True)
class ProductDetailContext:
    statistics: ProductStatistics

    inventory: InventorySummary
    recent_orders: list
    recent_reviews: list
    variants: list

# ==========================================================
# Base Query
# ==========================================================

def get_products_queryset():
    """
    Base queryset used throughout the dashboard product module.
    """

    return (
        Product.objects
        .select_related("brand", "product_type")
        .prefetch_related(
            "categories",
            "designs",
            "materials",
            "usages",
            "gallery",
            Prefetch(
                "variants",
                queryset=(
                    ProductVariant.objects
                    .select_related("color", "size", "finish")
                    .prefetch_related("prices")
                    .order_by(
                        "sort_order",
                        "id",
                    )
                ),
            ),
        )
        .annotate(
            variant_count=Count("variants", distinct=True),
            active_variant_count=Count("variants", filter=Q(variants__is_active=True), distinct=True),
            variant_stock=Coalesce(Sum("variants__stock", filter=Q(variants__is_active=True)), 0),
            usd_min_price=Min("variants__prices__amount", filter=Q(variants__prices__currency=Currency.USD)),
            usd_max_price=Max("variants__prices__amount", filter=Q(variants__prices__currency=Currency.USD)),
            irt_min_price=Min("variants__prices__amount", filter=Q(variants__prices__currency=Currency.TOMAN)),
            irt_max_price=Max("variants__prices__amount", filter=Q(variants__prices__currency=Currency.TOMAN)),
        )
        .order_by("-created_at")
    )


# ==========================================================
# Filtering
# ==========================================================

def filter_products(
    queryset,
    *,
    search="",
    category="",
    brand="",
    active="",
    featured="",
):
    """
    Apply dashboard product filters.
    """

    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(name_fa__icontains=search)
            | Q(sku__icontains=search)
            | Q(series_name__icontains=search)
            | Q(series_name_fa__icontains=search)
            | Q(designs__name__icontains=search)
            | Q(designs__name_fa__icontains=search)
            | Q(brand__name__icontains=search)
            | Q(brand__name_fa__icontains=search)
            | Q(categories__name__icontains=search)
            | Q(categories__name_fa__icontains=search)
            | Q(materials__name__icontains=search)
            | Q(materials__name_fa__icontains=search)
            | Q(usages__name__icontains=search)
            | Q(usages__name_fa__icontains=search)
        ).distinct()

    if category:
        queryset = queryset.filter(categories__id=category).distinct()

    if brand:
        queryset = queryset.filter(brand_id=brand)

    if active in ("true", "false"):
        queryset = queryset.filter(is_active=(active == "true"))

    if featured in ("true", "false"):
        queryset = queryset.filter(is_featured=(featured == "true"))

    return queryset


# ==========================================================
# Sorting
# ==========================================================

def sort_products(queryset, *, sort="newest"):
    """
    Apply dashboard product sorting.
    """

    sorting = {
        "newest": ("-created_at",),
        "oldest": ("created_at",),
        "name": ("name",),
        "updated": ("-updated_at",),
        "stock": ("variant_stock",),
    }

    return queryset.order_by(*sorting.get(sort, ("-created_at",)))


# ==========================================================
# Product Catalog
# ==========================================================

def get_products(
    *,
    search="",
    category="",
    brand="",
    active="",
    featured="",
    sort="newest",
):
    """
    Return the filtered and sorted dashboard product queryset.
    """

    queryset = get_products_queryset()

    queryset = filter_products(
        queryset,
        search=search,
        category=category,
        brand=brand,
        active=active,
        featured=featured,
    )

    queryset = sort_products(
        queryset,
        sort=sort,
    )

    return queryset


# ==========================================================
# Single Product
# ==========================================================

def get_product(*, pk):
    """
    Return a single product for dashboard management.
    """

    return get_object_or_404(
        get_products_queryset(),
        pk=pk,
    )
# ==========================================================
# Filter Options
# ==========================================================

def get_product_categories():
    """
    Return categories used by the dashboard product filters.
    """

    return (
        Category.objects
        .order_by("name")
    )


def get_product_brands():
    """
    Return a sorted list of unique product brands.
    """

    return (
        Brand.objects
        .filter(products__isnull=False)
        .distinct()
        .order_by("name")
    )

# ==========================================================
# Product Detail
# ==========================================================

def get_product_statistics(product):
    """
    Return dashboard statistics for a product.
    """

    variants = list(product.variants.all())

    return ProductStatistics(
        gallery_images=product.gallery.count(),
        variants=product.variant_count,
        active_variants=sum(variant.is_active for variant in variants),
        total_stock=sum(variant.stock for variant in variants if variant.is_active),
    )

def get_product_inventory_summary(product):
    """
    Return inventory information for the dashboard.
    """

    variants = [variant for variant in product.variants.all() if variant.is_active]
    total_stock = sum(variant.stock for variant in variants)

    return InventorySummary(
        total_stock=total_stock,
        active_variants=len(variants),
        in_stock_variants=sum(variant.stock > 0 for variant in variants),
        low_stock_variants=sum(variant.is_low_stock for variant in variants),
        out_of_stock_variants=sum(variant.stock == 0 for variant in variants),
    )

def get_product_recent_orders(product, *, limit=5):
    """
    Return recent orders containing this product.
    """

    return (
        OrderItem.objects
        .filter(product=product)
        .select_related("order")
        .order_by("-order__created_at")[:limit]
    )

def get_product_recent_reviews(product, *, limit=5):
    """
    Return the most recent approved reviews.
    """

    return (
        Review.objects
        .filter(
            product=product,
            is_approved=True,
        )
        .select_related("user")
        .order_by("-created_at")[:limit]
    )

def get_product_variants(product):
    return (
        product.variants
        .select_related("color", "size", "finish")
        .prefetch_related("prices")
        .order_by("sort_order", "id")
    )


def get_product_variant(*, product, variant_pk):
    """Return a variant only when it belongs to the requested product."""

    return get_object_or_404(
        ProductVariant.objects.select_related("product", "color", "size", "finish").prefetch_related("prices"),
        pk=variant_pk,
        product=product,
    )

def get_product_detail_context(product):
    """
    Return all dashboard detail data for a product.
    """

    return ProductDetailContext(
        statistics=get_product_statistics(product),
        inventory=get_product_inventory_summary(product),
        variants=get_product_variants(product),
        recent_orders=list(get_product_recent_orders(product)),
        recent_reviews=list(get_product_recent_reviews(product)),
    )
