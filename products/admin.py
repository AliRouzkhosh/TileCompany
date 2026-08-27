from django.contrib import admin

from .models import (
    Brand,
    Category,
    Color,
    Design,
    Finish,
    Material,
    Product,
    ProductImage,
    ProductVariant,
    VariantPrice,
    Size,
    Usage,
)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = (
        "sku",
        "size",
        "color",
        "finish",
        "price_unit",
        "stock",
        "low_stock_threshold",
        "is_active",
        "sort_order",
    )
    autocomplete_fields = ("size", "color", "finish")

    @staticmethod
    def _generic(parent):
        return bool(parent and parent.product_type_id and parent.product_type.code != "tile")

    def has_add_permission(self, request, obj=None):
        return False if self._generic(obj) else super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return False if self._generic(obj) else super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False if self._generic(obj) else super().has_delete_permission(request, obj)


class NamedTaxonomyAdmin(admin.ModelAdmin):
    list_display = ("name", "name_fa", "slug")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "name_fa")


class VariantPriceInline(admin.TabularInline):
    model = VariantPrice
    extra = 0
    fields = ("currency", "amount", "compare_at_amount")


@admin.register(Category)
class CategoryAdmin(NamedTaxonomyAdmin):
    pass


@admin.register(Brand)
class BrandAdmin(NamedTaxonomyAdmin):
    pass


@admin.register(Design)
class DesignAdmin(NamedTaxonomyAdmin):
    pass


@admin.register(Material)
class MaterialAdmin(NamedTaxonomyAdmin):
    pass


@admin.register(Color)
class ColorAdmin(NamedTaxonomyAdmin):
    pass


@admin.register(Size)
class SizeAdmin(NamedTaxonomyAdmin):
    list_display = ("name", "name_fa", "slug", "length_mm", "width_mm")


@admin.register(Finish)
class FinishAdmin(NamedTaxonomyAdmin):
    pass


@admin.register(Usage)
class UsageAdmin(NamedTaxonomyAdmin):
    pass


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "sku",
        "category_list",
        "material_list",
        "brand",
        "is_active",
        "is_featured",
    )
    list_filter = (
        "categories",
        "brand",
        "designs",
        "materials",
        "usages",
        "is_active",
        "is_featured",
    )
    search_fields = (
        "name",
        "name_fa",
        "sku",
        "description",
        "description_fa",
        "brand__name",
        "brand__name_fa",
        "series_name",
        "series_name_fa",
        "designs__name",
        "designs__name_fa",
    )
    prepopulated_fields = {"slug": ("name",)}
    # Publication state must never be a change-list shortcut. Tile remains
    # editable through its detail/dashboard workflow; generic rows use the
    # controlled publication services.
    list_editable = ("is_featured",)
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("brand",)
    filter_horizontal = ("categories", "designs", "materials", "usages")
    inlines = [ProductImageInline, ProductVariantInline]

    fieldsets = (
        (None, {"fields": (
            "name",
            "name_fa",
            "slug",
            "categories",
            "description",
            "description_fa",
        )}),
        ("Catalog", {"fields": (
            "brand",
            "series_name",
            "series_name_fa",
            "designs",
            "materials",
            "usages",
        )}),
        ("Catalog code", {"fields": (
            "sku",
        )}),
        ("Coverage", {"fields": ("tiles_per_box", "coverage_per_box_m2")}),
        ("Media & Visibility", {"fields": ("image", "is_active", "is_featured")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("brand", "product_type")
            .prefetch_related("categories", "designs", "materials", "usages")
        )

    def has_change_permission(self, request, obj=None):
        if obj and obj.product_type_id and obj.product_type.code != "tile":
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.product_type_id and obj.product_type.code != "tile":
            return False
        return super().has_delete_permission(request, obj)

    def category_list(self, obj):
        return ", ".join(category.name for category in obj.categories.all()) or "-"
    category_list.short_description = "Categories"

    def material_list(self, obj):
        return ", ".join(material.name for material in obj.materials.all()) or "-"
    material_list.short_description = "Materials"


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = (
        "sku",
        "product",
        "size",
        "color",
        "finish",
        "price_summary",
        "stock",
        "low_stock_threshold",
        "is_active",
        "sort_order",
    )
    list_filter = ("size", "color", "finish", "is_active")
    search_fields = ("sku", "product__name", "product__name_fa", "product__sku")
    autocomplete_fields = ("product", "size", "color", "finish")
    inlines = [VariantPriceInline]

    def has_add_permission(self, request):
        # Standalone admin creation cannot safely choose between the Tile
        # compatibility and generic publication policies before submission.
        return False

    def has_change_permission(self, request, obj=None):
        if obj and obj.product.product_type_id and obj.product.product_type.code != "tile":
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and obj.product.product_type_id and obj.product.product_type.code != "tile":
            return False
        return super().has_delete_permission(request, obj)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("product", "product__product_type", "size", "color", "finish")
            .prefetch_related("prices")
        )

    def price_summary(self, obj):
        return "; ".join(f"{price.currency} {price.amount}" for price in obj.prices.all()) or "-"
    price_summary.short_description = "Prices"
