from django.db.models import Count, Prefetch, Q

from products.models import (
    AttributeDefinition,
    AttributeOption,
    Category,
    ProductType,
    ProductTypeAttribute,
)


def get_product_types():
    return ProductType.objects.annotate(
        product_count=Count("products", distinct=True),
        binding_count=Count("attribute_bindings", distinct=True),
    ).order_by("sort_order", "name", "id")


def get_product_type_schema(product_type):
    bindings = ProductTypeAttribute.objects.filter(product_type=product_type).select_related(
        "definition"
    ).prefetch_related(
        Prefetch(
            "option_restrictions",
            queryset=product_type_option_memberships_queryset(),
        )
    ).annotate(
        product_value_count=Count("product_values", distinct=True),
        variant_value_count=Count("variant_values", distinct=True),
    ).order_by("scope", "sort_order", "id")
    return {
        "product": [item for item in bindings if item.scope == "product"],
        "variant_dimensions": [item for item in bindings if item.is_variant_dimension],
        "variant_specifications": [
            item for item in bindings if item.scope == "variant" and not item.is_variant_dimension
        ],
    }


def product_type_option_memberships_queryset():
    from products.models import ProductTypeAttributeOption
    return ProductTypeAttributeOption.objects.select_related("option").order_by(
        "sort_order", "option__sort_order", "id"
    )


def get_definitions():
    return AttributeDefinition.objects.annotate(
        option_count=Count("options", distinct=True),
        binding_count=Count("product_type_bindings", distinct=True),
    ).order_by("name", "id")


def get_definition_options(definition):
    return AttributeOption.objects.filter(definition=definition).annotate(
        product_usage_count=Count("product_values", distinct=True),
        variant_usage_count=Count("variant_values", distinct=True),
        membership_count=Count("applicability_restrictions", distinct=True),
        tile_membership_count=Count(
            "applicability_restrictions",
            filter=Q(applicability_restrictions__binding__product_type__code="tile"),
            distinct=True,
        ),
    ).order_by("sort_order", "name", "id")


def get_category_tree():
    return Category.objects.select_related("parent").annotate(
        product_count=Count("categorized_products", distinct=True),
        child_count=Count("children", distinct=True),
        primary_count=Count("primary_products", distinct=True),
    ).order_by("parent_id", "sort_order", "name", "id")
