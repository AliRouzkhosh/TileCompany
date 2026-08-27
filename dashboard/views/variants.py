from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from dashboard.forms import DashboardProductVariantForm
from dashboard.permissions import dashboard_permission_required
from dashboard.selectors import get_product, get_product_variant, get_product_variants
from dashboard.services import add_variant_gallery_images, create_product_variant, update_product_variant
from TileCompany.uploads import validate_raster_images
from products.models import VariantImage


def _generic_variant_guard(request, product):
    if product.product_type_id and product.product_type.code != "tile":
        messages.error(
            request,
            _("Generic Variant management is unavailable until F2C."),
        )
        return redirect("dashboard:products:generic_detail", pk=product.pk)
    return None


def _add_domain_errors(form, error):
    if hasattr(error, "message_dict"):
        for field, field_messages in error.message_dict.items():
            target = field if field in form.fields else None
            for message in field_messages:
                form.add_error(target, message)
    else:
        for message in error.messages:
            form.add_error(None, message)


@dashboard_permission_required("products.view_product", "products.view_productvariant")
def variant_catalog(request, product_pk):
    """List all variants for a product, including inactive variants."""

    product = get_product(pk=product_pk)
    if response := _generic_variant_guard(request, product):
        return response
    return render(
        request,
        "dashboard/variants/catalog.html",
        {
            "page_title": _("Variants for %(name)s") % {"name": product.name},
            "product": product,
            "variants": get_product_variants(product),
        },
    )


@dashboard_permission_required("products.add_productvariant")
def variant_create(request, product_pk):
    """Create a variant for one specific product."""

    product = get_product(pk=product_pk)
    if response := _generic_variant_guard(request, product):
        return response
    form = DashboardProductVariantForm(
        request.POST or None,
        can_change_inventory=request.user.has_perm("products.change_inventory"),
        can_change_pricing=request.user.has_perm("products.change_pricing"),
        can_publish=request.user.has_perm("products.publish_product"),
    )

    if request.method == "POST" and form.is_valid():
        if request.FILES.getlist("gallery_images") and not request.user.has_perm("products.add_variantimage"):
            raise PermissionDenied
        try:
            gallery_images = validate_raster_images(request.FILES.getlist("gallery_images"))
            variant = create_product_variant(
                product=product, form=form, user=request.user,
                write_prices=request.user.has_perm("products.change_pricing"),
            )
        except ValidationError as error:
            _add_domain_errors(form, error)
        else:
            add_variant_gallery_images(variant=variant, files=gallery_images)
            messages.success(request, _("Variant created successfully."))
            return redirect("dashboard:products:variants:catalog", product_pk=product.pk)

    return render(
        request,
        "dashboard/variants/form.html",
        {
            "page_title": _("Add Variant for %(name)s") % {"name": product.name},
            "product": product,
            "form": form,
        },
    )


@dashboard_permission_required("products.change_productvariant")
def variant_edit(request, product_pk, variant_pk):
    """Edit a variant only through its owning product URL."""

    product = get_product(pk=product_pk)
    if response := _generic_variant_guard(request, product):
        return response
    variant = get_product_variant(product=product, variant_pk=variant_pk)
    form = DashboardProductVariantForm(
        request.POST or None, instance=variant,
        can_change_inventory=request.user.has_perm("products.change_inventory"),
        can_change_pricing=request.user.has_perm("products.change_pricing"),
        can_publish=request.user.has_perm("products.publish_product"),
    )

    if request.method == "POST" and form.is_valid():
        if request.FILES.getlist("gallery_images") and not request.user.has_perm("products.add_variantimage"):
            raise PermissionDenied
        try:
            gallery_images = validate_raster_images(request.FILES.getlist("gallery_images"))
            update_product_variant(
                variant=variant, form=form, user=request.user,
                write_prices=request.user.has_perm("products.change_pricing"),
            )
        except ValidationError as error:
            _add_domain_errors(form, error)
        else:
            add_variant_gallery_images(variant=variant, files=gallery_images)
            messages.success(request, _("Variant updated successfully."))
            return redirect("dashboard:products:variants:catalog", product_pk=product.pk)

    return render(
        request,
        "dashboard/variants/form.html",
        {
            "page_title": _("Edit %(sku)s") % {"sku": variant.sku},
            "product": product,
            "variant": variant,
            "form": form,
        },
    )


@dashboard_permission_required("products.change_productvariant", "products.delete_variantimage")
@require_POST
def variant_gallery_delete(request, product_pk, variant_pk, image_pk):
    product = get_product(pk=product_pk)
    if response := _generic_variant_guard(request, product):
        return response
    variant = get_product_variant(product=product, variant_pk=variant_pk)
    image = VariantImage.objects.filter(pk=image_pk, variant=variant).first()
    if image:
        image.image.delete(save=False)
        image.delete()
        messages.success(request, _("Variant image removed."))
    return redirect("dashboard:products:variants:edit", product_pk=product.pk, variant_pk=variant.pk)
