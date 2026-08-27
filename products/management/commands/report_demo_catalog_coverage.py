"""Read-only coverage report for the generic demo catalogue."""

from django.core.management.base import BaseCommand
from django.db.models import Count

from products.models import Product, ProductAttributeValue, ProductTypeAttribute, VariantAttributeValue


class Command(BaseCommand):
    help = "Report represented generic attribute values for the demo catalogue without mutation."

    PRODUCT_TYPES = ("ceramic-tile", "tile-adhesive", "grout", "installation-additive")

    def handle(self, *args, **options):
        bindings = ProductTypeAttribute.objects.filter(
            product_type__code__in=self.PRODUCT_TYPES,
            is_active=True,
        ).select_related("product_type", "definition").order_by("product_type__code", "sort_order")
        for binding in bindings:
            products = Product.objects.filter(product_type=binding.product_type, is_active=True)
            values = (
                ProductAttributeValue.objects.filter(product__in=products, binding=binding)
                if binding.scope == ProductTypeAttribute.Scope.PRODUCT
                else VariantAttributeValue.objects.filter(variant__product__in=products, binding=binding)
            )
            populated = (
                values.values("product_id").distinct().count()
                if binding.scope == ProductTypeAttribute.Scope.PRODUCT
                else values.values("variant__product_id").distinct().count()
            )
            represented = values.values("option_value__code").annotate(
                total=Count("product_id" if binding.scope == ProductTypeAttribute.Scope.PRODUCT else "variant_id", distinct=True)
            ).order_by("option_value__code")
            formatted = ", ".join(f"{row['option_value__code']}={row['total']}" for row in represented)
            self.stdout.write(
                f"{binding.product_type.code} | {binding.definition.code} | "
                f"{binding.scope} | products={products.count()} | populated={populated} | {formatted}"
            )
