"""Transactional write boundary for the generic catalog-schema dashboard."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from dashboard.catalog_schema_policy import (
    ensure_binding_mutable,
    ensure_definition_mutable,
    ensure_membership_mutable,
    ensure_option_mutable,
    ensure_product_type_mutable,
    validate_binding_change,
)
from products.models import ProductTypeAttributeOption


def _protect_active_product_types(product_type_ids):
    from products.publication_services import (
        PublicationBlockedError,
        require_active_catalog_ready,
    )
    try:
        require_active_catalog_ready(product_type_ids)
    except PublicationBlockedError as error:
        raise ValidationError([
            _("Active catalog publication would become invalid: %(issue)s") % {"issue": issue.message}
            for issue in error.issues
        ]) from error


def _validated_save(instance):
    instance.full_clean()
    try:
        instance.save()
    except IntegrityError as exc:
        raise ValidationError("The stable code or schema relationship already exists.") from exc
    return instance


@transaction.atomic
def create_product_type(form):
    return _validated_save(form.save(commit=False))


@transaction.atomic
def update_product_type(product_type, form):
    persisted = type(product_type).objects.select_for_update().get(pk=product_type.pk)
    ensure_product_type_mutable(persisted)
    saved = _validated_save(form.save(commit=False))
    _protect_active_product_types([saved.pk])
    return saved


@transaction.atomic
def retire_product_type(product_type):
    product_type = type(product_type).objects.select_for_update().get(pk=product_type.pk)
    ensure_product_type_mutable(product_type)
    product_type.is_active = False
    saved = _validated_save(product_type)
    _protect_active_product_types([saved.pk])
    return saved


@transaction.atomic
def delete_product_type(product_type):
    ensure_product_type_mutable(product_type)
    if product_type.products.exists() or product_type.attribute_bindings.exists():
        raise ValidationError("Referenced ProductTypes cannot be deleted; deactivate instead.")
    product_type.delete()


@transaction.atomic
def create_attribute_definition(form):
    return _validated_save(form.save(commit=False))


@transaction.atomic
def update_attribute_definition(definition, form):
    persisted = type(definition).objects.select_for_update().get(pk=definition.pk)
    ensure_definition_mutable(persisted)
    candidate = form.save(commit=False)
    if candidate.data_type != persisted.data_type and (
        persisted.options.exists()
        or persisted.product_type_bindings.exists()
    ):
        raise ValidationError(
            "Data type cannot change after options or ProductType bindings exist."
        )
    product_type_ids = list(persisted.product_type_bindings.values_list("product_type_id", flat=True))
    saved = _validated_save(candidate)
    _protect_active_product_types(product_type_ids)
    return saved


@transaction.atomic
def retire_attribute_definition(definition):
    definition = type(definition).objects.select_for_update().get(pk=definition.pk)
    ensure_definition_mutable(definition)
    definition.is_active = False
    product_type_ids = list(definition.product_type_bindings.values_list("product_type_id", flat=True))
    saved = _validated_save(definition)
    _protect_active_product_types(product_type_ids)
    return saved


@transaction.atomic
def delete_attribute_definition(definition):
    ensure_definition_mutable(definition)
    if definition.options.exists() or definition.product_type_bindings.exists():
        raise ValidationError("Referenced definitions cannot be deleted; deactivate instead.")
    definition.delete()


@transaction.atomic
def create_attribute_option(definition, form):
    if definition.data_type != "option":
        raise ValidationError("Only option-valued definitions can own options.")
    option = form.save(commit=False)
    option.definition = definition
    return _validated_save(option)


@transaction.atomic
def update_attribute_option(option, form):
    persisted = type(option).objects.select_for_update().get(pk=option.pk)
    ensure_option_mutable(persisted)
    product_type_ids = list(
        persisted.definition.product_type_bindings.values_list("product_type_id", flat=True)
    )
    saved = _validated_save(form.save(commit=False))
    _protect_active_product_types(product_type_ids)
    return saved


@transaction.atomic
def retire_attribute_option(option):
    option = type(option).objects.select_for_update().select_related("definition").get(pk=option.pk)
    ensure_option_mutable(option)
    option.is_active = False
    product_type_ids = list(
        option.definition.product_type_bindings.values_list("product_type_id", flat=True)
    )
    saved = _validated_save(option)
    _protect_active_product_types(product_type_ids)
    return saved


@transaction.atomic
def delete_attribute_option(option):
    ensure_option_mutable(option)
    if (
        option.product_values.exists()
        or option.variant_values.exists()
        or option.applicability_restrictions.exists()
    ):
        raise ValidationError("Referenced options cannot be deleted; deactivate instead.")
    option.delete()


@transaction.atomic
def create_binding(product_type, form):
    ensure_product_type_mutable(product_type)
    binding = form.save(commit=False)
    binding.product_type = product_type
    validate_binding_change(binding, binding)
    return _validated_save(binding)


@transaction.atomic
def update_binding(binding, form):
    persisted = type(binding).objects.select_for_update().get(pk=binding.pk)
    ensure_binding_mutable(persisted)
    candidate = form.save(commit=False)
    validate_binding_change(persisted, candidate)
    saved = _validated_save(candidate)
    _protect_active_product_types([saved.product_type_id])
    return saved


@transaction.atomic
def retire_binding(binding):
    ensure_binding_mutable(binding)
    candidate = type(binding).objects.select_for_update().get(pk=binding.pk)
    candidate.is_active = False
    validate_binding_change(binding, candidate)
    saved = _validated_save(candidate)
    _protect_active_product_types([saved.product_type_id])
    return saved


@transaction.atomic
def delete_binding(binding):
    ensure_binding_mutable(binding)
    if binding.product_values.exists() or binding.variant_values.exists():
        raise ValidationError("Bindings with values cannot be deleted; deactivate instead.")
    if binding.is_variant_dimension and binding.product_type.products.filter(
        variants__isnull=False
    ).exists():
        raise ValidationError(
            "A dimension cannot be removed while variants exist; use a controlled migration."
        )
    binding.delete()


@transaction.atomic
def create_membership(binding, form):
    ensure_membership_mutable(binding)
    if binding.definition.data_type != "option":
        raise ValidationError("Only option-valued bindings support allowed-option membership.")
    membership = form.save(commit=False)
    membership.binding = binding
    return _validated_save(membership)


@transaction.atomic
def update_membership(membership, form):
    persisted = type(membership).objects.select_for_update().select_related("binding").get(pk=membership.pk)
    ensure_membership_mutable(persisted.binding)
    candidate = form.save(commit=False)
    candidate.binding = membership.binding
    saved = _validated_save(candidate)
    _protect_active_product_types([saved.binding.product_type_id])
    return saved


@transaction.atomic
def retire_membership(membership):
    membership = type(membership).objects.select_for_update().select_related("binding").get(pk=membership.pk)
    ensure_membership_mutable(membership.binding)
    membership.is_active = False
    saved = _validated_save(membership)
    _protect_active_product_types([saved.binding.product_type_id])
    return saved


@transaction.atomic
def delete_membership(membership, *, allow_unrestricted=False):
    ensure_membership_mutable(membership.binding)
    binding = membership.binding
    if binding.option_restrictions.count() == 1 and not allow_unrestricted:
        raise ValidationError(
            "Removing the final membership would enable unrestricted mode. "
            "Choose unrestricted mode explicitly."
        )
    if membership.option.product_values.filter(binding=binding).exists() or membership.option.variant_values.filter(
        binding=binding
    ).exists():
        raise ValidationError("Used memberships cannot be hard-deleted; deactivate instead.")
    membership.delete()


@transaction.atomic
def set_binding_unrestricted(binding, *, confirmed=False):
    ensure_membership_mutable(binding)
    if not confirmed:
        raise ValidationError("Explicit confirmation is required to enable unrestricted mode.")
    binding.option_restrictions.all().delete()


@transaction.atomic
def save_category(form):
    category = form.save(commit=False)
    return _validated_save(category)


@transaction.atomic
def retire_category(category):
    category.is_active = False
    return _validated_save(category)


@transaction.atomic
def delete_category(category):
    if category.children.exists():
        raise ValidationError("Categories with children cannot be deleted; move or deactivate them.")
    if category.categorized_products.exists() or category.primary_products.exists():
        raise ValidationError("Categories used by Products cannot be deleted; deactivate instead.")
    category.delete()
