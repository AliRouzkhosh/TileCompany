from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from dashboard.catalog_schema_policy import ensure_generic_variant_product
from dashboard.forms_generic_variant import GenericVariantForm
from dashboard.permissions import dashboard_permission_required
from dashboard.selectors.generic_variants import get_generic_variant, get_generic_variant_product, load_generic_variant_form_context
from dashboard.services import add_variant_gallery_images
from TileCompany.uploads import validate_raster_images
from dashboard.services_generic_variant import create_generic_variant, update_generic_variant
from products.catalog_read import read_product_detail
from products.publication_readiness import evaluate_product_publication_readiness
from products.publication_services import (
    PublicationBlockedError,
    publish_variant,
    unpublish_variant,
)


def _reject(request, product, *, for_creation=False):
    try:
        ensure_generic_variant_product(product, for_creation=for_creation)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
        if product.product_type_id and product.product_type.code == "tile":
            return redirect("dashboard:products:variants:catalog", product_pk=product.pk)
        return redirect("dashboard:products:generic_detail", pk=product.pk)


def _add_errors(form, error):
    for value in error.messages:
        form.add_error(None, value)


@dashboard_permission_required("products.view_product", "products.view_productvariant")
def generic_variant_catalog(request, product_pk):
    product = get_generic_variant_product(product_pk)
    if response := _reject(request, product):
        return response
    readiness = evaluate_product_publication_readiness(product)
    return render(request, "dashboard/generic_variants/catalog.html", {"product": product, "detail": read_product_detail(product), "readiness": readiness})


@dashboard_permission_required("products.add_productvariant")
def generic_variant_create(request, product_pk):
    product = get_generic_variant_product(product_pk)
    if response := _reject(request, product, for_creation=True):
        return response
    context = load_generic_variant_form_context(product)
    form = GenericVariantForm(
        request.POST or None, product=product, schema_context=context,
        can_change_inventory=request.user.has_perm("products.change_inventory"),
        can_change_pricing=request.user.has_perm("products.change_pricing"),
    )
    if request.method == "POST" and form.is_valid():
        if request.FILES.getlist("gallery_images") and not request.user.has_perm("products.add_variantimage"):
            raise PermissionDenied
        try:
            gallery_images = validate_raster_images(request.FILES.getlist("gallery_images"))
            variant = create_generic_variant(
                product=product, form=form, user=request.user,
                write_prices=request.user.has_perm("products.change_pricing"),
            )
        except ValidationError as error:
            _add_errors(form, error)
        except PublicationBlockedError as error:
            for issue in error.issues:
                form.add_error(None, issue.message)
        else:
            add_variant_gallery_images(variant=variant, files=gallery_images)
            messages.success(request, _("Generic Variant created as an inactive draft."))
            return redirect("dashboard:products:generic_variant_catalog", product_pk=product.pk)
    return render(request, "dashboard/generic_variants/form.html", {"product": product, "variant": None, "form": form, "title": _("Add Variant")})


@dashboard_permission_required("products.change_productvariant")
def generic_variant_edit(request, product_pk, variant_pk):
    product = get_generic_variant_product(product_pk)
    if response := _reject(request, product):
        return response
    variant = get_generic_variant(product, variant_pk)
    context = load_generic_variant_form_context(product, variant=variant)
    form = GenericVariantForm(
        request.POST or None, instance=variant, product=product, schema_context=context,
        can_change_inventory=request.user.has_perm("products.change_inventory"),
        can_change_pricing=request.user.has_perm("products.change_pricing"),
    )
    if request.method == "POST" and form.is_valid():
        if request.FILES.getlist("gallery_images") and not request.user.has_perm("products.add_variantimage"):
            raise PermissionDenied
        try:
            gallery_images = validate_raster_images(request.FILES.getlist("gallery_images"))
            variant = update_generic_variant(
                variant=variant, form=form, user=request.user,
                write_prices=request.user.has_perm("products.change_pricing"),
            )
        except ValidationError as error:
            _add_errors(form, error)
        except PublicationBlockedError as error:
            for issue in error.issues:
                form.add_error(None, issue.message)
        else:
            add_variant_gallery_images(variant=variant, files=gallery_images)
            messages.success(request, _("Generic Variant updated."))
            return redirect("dashboard:products:generic_variant_catalog", product_pk=product.pk)
    product_readiness = evaluate_product_publication_readiness(product)
    readiness = next(row for row in product_readiness.variant_results if row.variant_id == variant.pk)
    return render(request, "dashboard/generic_variants/form.html", {"product": product, "variant": variant, "form": form, "title": _("Edit %(sku)s") % {"sku": variant.sku}, "readiness": readiness})


def _transition_error_messages(request, error):
    for issue in error.issues:
        messages.error(request, issue.message)


@dashboard_permission_required("products.publish_product")
@require_POST
def generic_variant_publish(request, product_pk, variant_pk):
    product = get_generic_variant_product(product_pk)
    if response := _reject(request, product):
        return response
    variant = get_generic_variant(product, variant_pk)
    try:
        result = publish_variant(variant, user=request.user)
    except PublicationBlockedError as error:
        _transition_error_messages(request, error)
    else:
        messages.success(request, _("Variant published."))
        for warning in result.warnings:
            messages.warning(request, warning.message)
    return redirect("dashboard:products:generic_variant_catalog", product_pk=product.pk)


@dashboard_permission_required("products.publish_product")
@require_POST
def generic_variant_unpublish(request, product_pk, variant_pk):
    product = get_generic_variant_product(product_pk)
    if response := _reject(request, product):
        return response
    variant = get_generic_variant(product, variant_pk)
    try:
        unpublish_variant(variant, user=request.user)
    except PublicationBlockedError as error:
        _transition_error_messages(request, error)
    else:
        messages.success(request, _("Variant unpublished."))
    return redirect("dashboard:products:generic_variant_catalog", product_pk=product.pk)
