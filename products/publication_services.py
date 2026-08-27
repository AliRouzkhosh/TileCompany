"""Controlled, transactional publication state transitions."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from .models import (
    AttributeDefinition,
    AttributeOption,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    VariantAttributeValue,
    VariantPrice,
)
from .publication_readiness import (
    ProductPublicationReadinessDTO,
    PublicationIssueDTO,
    PublicationSeverity,
    VariantPublicationReadinessDTO,
    evaluate_product_publication_readiness,
    evaluate_publication_readiness,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PublicationTransitionResultDTO:
    object_type: str
    object_id: int
    sku: str
    previous_active_state: bool
    current_active_state: bool
    readiness: ProductPublicationReadinessDTO | VariantPublicationReadinessDTO

    @property
    def changed(self):
        return self.previous_active_state != self.current_active_state

    @property
    def warnings(self):
        return self.readiness.warnings

    @property
    def info(self):
        return self.readiness.info


class PublicationBlockedError(Exception):
    """Structured transition failure carrying stable readiness issue codes."""

    def __init__(self, *, readiness=None, issues=()):
        self.readiness = readiness
        self.issues = tuple(issues or (readiness.blockers if readiness else ()))
        super().__init__("; ".join(str(issue.message) for issue in self.issues))

    @property
    def issue_codes(self):
        return tuple(issue.code for issue in self.issues)


def _ensure_generic(product):
    if not product.product_type_id or product.product_type.code == "tile":
        raise ValidationError(_("Generic publication services cannot mutate Tile catalog state."))


def lock_product_publication_scope(product_id):
    """Lock the authoritative readiness scope before evaluating a transition."""

    product = Product.objects.select_for_update(of=("self",)).select_related(
        "product_type", "primary_category"
    ).get(pk=product_id)
    if product.product_type_id:
        ProductType.objects.select_for_update().get(pk=product.product_type_id)
        bindings = list(
            ProductTypeAttribute.objects.select_for_update().filter(
                product_type_id=product.product_type_id
            ).select_related("definition")
        )
        definition_ids = {binding.definition_id for binding in bindings}
        list(AttributeDefinition.objects.select_for_update().filter(pk__in=definition_ids))
        memberships = list(
            ProductTypeAttributeOption.objects.select_for_update().filter(
                binding_id__in=[binding.pk for binding in bindings]
            )
        )
        list(AttributeOption.objects.select_for_update().filter(
            definition_id__in=definition_ids
        ))
    list(ProductAttributeValue.objects.select_for_update().filter(product_id=product.pk))
    variants = list(ProductVariant.objects.select_for_update().filter(product_id=product.pk))
    variant_ids = [variant.pk for variant in variants]
    list(VariantAttributeValue.objects.select_for_update().filter(variant_id__in=variant_ids))
    list(VariantPrice.objects.select_for_update().filter(variant_id__in=variant_ids))
    list(product.categories.through.objects.select_for_update().filter(product_id=product.pk))
    return product


def _variant_result(product_readiness, variant_id):
    return next(row for row in product_readiness.variant_results if row.variant_id == variant_id)


def _log_transition(action, result, *, user=None):
    logger.info(
        "catalog_publication_transition",
        extra={
            "action": action,
            "object_type": result.object_type,
            "object_id": result.object_id,
            "sku": result.sku,
            "previous_active_state": result.previous_active_state,
            "current_active_state": result.current_active_state,
            "operator_id": getattr(user, "pk", None),
            "warning_codes": [issue.code for issue in result.warnings],
        },
    )


@transaction.atomic
def publish_variant(variant, *, user=None, required_currencies=None):
    product = lock_product_publication_scope(variant.product_id)
    _ensure_generic(product)
    persisted = ProductVariant.objects.get(pk=variant.pk, product=product)
    readiness = evaluate_product_publication_readiness(
        product, required_currencies=required_currencies
    )
    variant_readiness = _variant_result(readiness, persisted.pk)
    if not variant_readiness.ready:
        raise PublicationBlockedError(readiness=variant_readiness)
    previous = persisted.is_active
    if not previous:
        persisted.is_active = True
        persisted.save(update_fields=["is_active"])
    post = evaluate_product_publication_readiness(
        product, required_currencies=required_currencies
    )
    result = PublicationTransitionResultDTO(
        "variant", persisted.pk, persisted.sku, previous, True,
        _variant_result(post, persisted.pk),
    )
    _log_transition("publish_variant", result, user=user)
    return result


@transaction.atomic
def publish_product(product, *, user=None, required_currencies=None):
    persisted = lock_product_publication_scope(product.pk)
    _ensure_generic(persisted)
    readiness = evaluate_product_publication_readiness(
        persisted, required_currencies=required_currencies
    )
    if not readiness.ready:
        raise PublicationBlockedError(readiness=readiness)
    previous = persisted.is_active
    if not previous:
        persisted.is_active = True
        persisted.save(update_fields=["is_active", "updated_at"])
    post = evaluate_product_publication_readiness(
        persisted, required_currencies=required_currencies
    )
    result = PublicationTransitionResultDTO(
        "product", persisted.pk, persisted.sku, previous, True, post
    )
    _log_transition("publish_product", result, user=user)
    return result


@transaction.atomic
def unpublish_product(product, *, user=None, required_currencies=None):
    persisted = lock_product_publication_scope(product.pk)
    _ensure_generic(persisted)
    previous = persisted.is_active
    if previous:
        persisted.is_active = False
        persisted.save(update_fields=["is_active", "updated_at"])
    readiness = evaluate_product_publication_readiness(
        persisted, required_currencies=required_currencies
    )
    result = PublicationTransitionResultDTO(
        "product", persisted.pk, persisted.sku, previous, False, readiness
    )
    _log_transition("unpublish_product", result, user=user)
    return result


@transaction.atomic
def unpublish_variant(variant, *, user=None, required_currencies=None):
    product = lock_product_publication_scope(variant.product_id)
    _ensure_generic(product)
    persisted = ProductVariant.objects.get(pk=variant.pk, product=product)
    previous = persisted.is_active
    readiness = evaluate_product_publication_readiness(
        product, required_currencies=required_currencies
    )
    current = _variant_result(readiness, persisted.pk)
    if previous and product.is_active:
        other_active_ready = any(
            row.variant_id != persisted.pk and row.is_active and row.ready
            for row in readiness.variant_results
        )
        if not other_active_ready:
            issue = PublicationIssueDTO(
                "last_active_ready_variant",
                PublicationSeverity.BLOCK,
                _("This is the last active publication-ready Variant. Unpublish the Product first."),
                "is_active",
                persisted.sku,
            )
            raise PublicationBlockedError(readiness=current, issues=(issue,))
    if previous:
        persisted.is_active = False
        persisted.save(update_fields=["is_active"])
    post = evaluate_product_publication_readiness(
        product, required_currencies=required_currencies
    )
    result = PublicationTransitionResultDTO(
        "variant", persisted.pk, persisted.sku, previous, False,
        _variant_result(post, persisted.pk),
    )
    _log_transition("unpublish_variant", result, user=user)
    return result


def require_product_ready(product, *, required_currencies=None):
    readiness = evaluate_product_publication_readiness(
        product, required_currencies=required_currencies
    )
    if not readiness.ready:
        raise PublicationBlockedError(readiness=readiness)
    return readiness


def require_variant_ready(variant, *, required_currencies=None):
    readiness = evaluate_product_publication_readiness(
        variant.product, required_currencies=required_currencies
    )
    result = _variant_result(readiness, variant.pk)
    if not result.ready:
        raise PublicationBlockedError(readiness=result)
    return result


def require_active_catalog_ready(product_type_ids):
    """Reject a schema mutation that invalidates any active generic row."""

    products = list(
        Product.objects.filter(product_type_id__in=set(product_type_ids))
        .exclude(product_type__code="tile")
    )
    if not products:
        return
    batch = evaluate_publication_readiness(products)
    invalid = []
    for product in batch.product_results:
        if product.is_active and not product.ready:
            invalid.extend(product.blockers)
        for variant in product.variant_results:
            if variant.is_active and not variant.ready:
                invalid.extend(variant.blockers)
    if invalid:
        raise PublicationBlockedError(issues=tuple(invalid))
