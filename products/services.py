"""Write-side pricing operations for the products domain."""

from django.db import transaction

from .currency import Currency
from .models import ProductVariant, VariantPrice
from .catalog_domain import validate_primary_category_membership


@transaction.atomic
def set_variant_price(
    *, variant, currency, amount, compare_at_amount=None,
    enforce_active_readiness=True,
):
    """Create or update one explicit currency price for a product variant."""

    currency = Currency(currency).value
    variant = ProductVariant.objects.select_for_update(of=("self",)).select_related(
        "product__product_type"
    ).get(pk=variant.pk)
    price = (
        VariantPrice.objects.select_for_update()
        .filter(variant=variant, currency=currency)
        .first()
    )
    if price is None:
        price = VariantPrice(
            variant=variant,
            currency=currency,
            amount=amount,
            compare_at_amount=compare_at_amount,
        )
    else:
        price.amount = amount
        price.compare_at_amount = compare_at_amount

    price.full_clean()
    price.save()
    if enforce_active_readiness and variant.is_active and variant.product.product_type_id:
        product_type = variant.product.product_type
        if product_type.code != "tile":
            from .publication_services import require_variant_ready
            require_variant_ready(variant)
    return price


@transaction.atomic
def set_product_primary_category(*, product, category):
    """Set or clear the canonical category while preserving M2M membership."""
    validate_primary_category_membership(product, category)
    product.primary_category = category
    product.save(update_fields=["primary_category", "updated_at"])
    return product
