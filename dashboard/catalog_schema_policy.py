"""Lifecycle and compatibility policy for generic catalog schema writes."""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

from products.models import ProductAttributeValue, VariantAttributeValue


TILE_CODE = "tile"


@dataclass(frozen=True)
class SchemaImpact:
    products: int = 0
    variants: int = 0
    product_values: int = 0
    variant_values: int = 0


def product_type_impact(product_type):
    products = product_type.products.all()
    return SchemaImpact(
        products=products.count(),
        variants=sum(product.variants.count() for product in products),
        product_values=ProductAttributeValue.objects.filter(product__product_type=product_type).count(),
        variant_values=VariantAttributeValue.objects.filter(
            variant__product__product_type=product_type
        ).count(),
    )


def binding_impact(binding):
    return SchemaImpact(
        products=binding.product_type.products.count(),
        variants=sum(
            product.variants.count() for product in binding.product_type.products.all()
        ),
        product_values=binding.product_values.count(),
        variant_values=binding.variant_values.count(),
    )


def option_impact(option):
    return SchemaImpact(
        product_values=option.product_values.count(),
        variant_values=option.variant_values.count(),
    )


def ensure_product_type_mutable(product_type):
    if product_type.code == TILE_CODE:
        raise ValidationError(
            "Tile schema is compatibility-protected while legacy Tile writes are authoritative."
        )


def ensure_definition_mutable(definition):
    if definition.product_type_bindings.filter(product_type__code=TILE_CODE).exists():
        raise ValidationError(
            "This definition is part of the compatibility-protected Tile schema."
        )


def ensure_option_mutable(option):
    if option.applicability_restrictions.filter(
        binding__product_type__code=TILE_CODE
    ).exists():
        raise ValidationError(
            "This mapped Tile option is managed through the legacy taxonomy dashboard."
        )


def ensure_binding_mutable(binding):
    ensure_product_type_mutable(binding.product_type)


def ensure_membership_mutable(binding):
    ensure_product_type_mutable(binding.product_type)


def validate_binding_change(binding, candidate):
    """Block changes that require controlled value/signature migrations."""
    if not binding.pk:
        impact = product_type_impact(candidate.product_type)
        if candidate.is_variant_dimension and impact.variants:
            raise ValidationError(
                f"Cannot add a variant dimension while {impact.variants} variants exist; "
                "this requires a controlled catalog migration."
            )
        if candidate.is_required and candidate.scope == "product" and impact.products:
            raise ValidationError(
                f"Cannot add a required Product attribute while {impact.products} products lack values."
            )
        if candidate.is_required and candidate.scope == "variant" and impact.variants:
            raise ValidationError(
                f"Cannot add a required Variant attribute while {impact.variants} variants lack values."
            )
        return

    ensure_binding_mutable(binding)
    impact = binding_impact(binding)
    structural_fields = (
        "definition_id",
        "scope",
        "cardinality",
        "is_variant_dimension",
        "is_required",
        "is_active",
    )
    changed = {
        field for field in structural_fields
        if getattr(binding, field) != getattr(candidate, field)
    }
    if impact.variants and (
        binding.is_variant_dimension
        or candidate.is_variant_dimension
    ) and changed:
        raise ValidationError(
            f"Variant dimension structure cannot change while {impact.variants} variants exist; "
            "this requires a controlled catalog migration."
        )
    if (impact.product_values or impact.variant_values) and changed & {
        "definition_id", "scope", "cardinality"
    }:
        raise ValidationError(
            "Definition, scope, and cardinality cannot change while attribute values exist."
        )
    if candidate.is_required and not binding.is_required:
        owner_count = impact.products if candidate.scope == "product" else impact.variants
        value_count = impact.product_values if candidate.scope == "product" else impact.variant_values
        if value_count < owner_count:
            raise ValidationError(
                f"Cannot make this attribute required: {owner_count - value_count} catalog records lack a value."
            )


def ensure_generic_product_type(product_type, *, for_creation=False):
    if product_type is None:
        raise ValidationError("ProductType must be selected explicitly.")
    if product_type.code == TILE_CODE:
        raise ValidationError(
            "Tile Products must use the existing legacy-authoritative Product workflow."
        )
    if for_creation and not product_type.is_active:
        raise ValidationError("Inactive ProductTypes cannot be used for new Products.")


def validate_generic_product_write(candidate, *, persisted=None):
    """ProductType ownership and ordinary-editor state-transition guards."""
    ensure_generic_product_type(candidate.product_type, for_creation=persisted is None)
    if persisted is not None and persisted.product_type_id != candidate.product_type_id:
        raise ValidationError(
            "ProductType cannot change through ordinary Product editing; this requires a catalog migration."
        )
    if persisted is None and candidate.is_active:
        raise ValidationError(
            _("Generic Products are created as inactive drafts; use the publication service.")
        )
    if persisted is not None and candidate.is_active != persisted.is_active:
        raise ValidationError(_("Active state can change only through the publication service."))


def ensure_generic_variant_product(product, *, for_creation=False):
    ensure_generic_product_type(product.product_type, for_creation=for_creation)
    if for_creation and not product.product_type.is_active:
        raise ValidationError("Inactive ProductTypes cannot be used for new Variants.")


def validate_generic_variant_write(candidate, *, product, persisted=None):
    ensure_generic_variant_product(product, for_creation=persisted is None)
    if candidate.product_id != product.pk:
        raise ValidationError("A Variant cannot move between Products.")
    if persisted is not None and persisted.product_id != candidate.product_id:
        raise ValidationError("A Variant cannot move between Products.")
    if persisted is None and candidate.is_active:
        raise ValidationError(
            _("Generic Variants are created as inactive drafts; use the publication service.")
        )
    if persisted is not None and candidate.is_active != persisted.is_active:
        raise ValidationError(_("Active state can change only through the publication service."))
    tile_fields = (
        "color_id", "size_id", "finish_id", "length_mm", "width_mm", "thickness_mm",
        "tiles_per_box", "coverage_per_box_m2",
    )
    if any(getattr(candidate, field) is not None for field in tile_fields):
        raise ValidationError("Generic Variants cannot store legacy Tile attributes or packaging facts.")
