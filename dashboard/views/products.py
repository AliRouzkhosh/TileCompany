################# dashboard/views/products.py ##############3
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect
from django.utils.translation import gettext as _, ngettext
from django.views.decorators.http import require_POST

from dashboard.permissions import dashboard_permission_required
from dashboard.selectors import (
    get_product,
    get_product_brands,
    get_product_categories,
    get_products,
    paginate_queryset,
    get_product_detail_context,
)

from dashboard.forms import DashboardProductForm
from dashboard.services import (
    add_product_gallery_images,
    create_product,
    update_product,
)
from TileCompany.uploads import validate_raster_images
from products.models import ProductImage


def _add_domain_errors(form, error):
    if hasattr(error, "message_dict"):
        for field, field_messages in error.message_dict.items():
            target = field if field in form.fields else None
            for message in field_messages:
                form.add_error(target, message)
    else:
        for message in error.messages:
            form.add_error(None, message)


@dashboard_permission_required("products.view_product")
def product_catalog(request):
    """
    Display the dashboard product catalog.
    """

    filters = {
        "search": request.GET.get("search", "").strip(),
        "category": request.GET.get("category", ""),
        "brand": request.GET.get("brand", ""),
        "active": request.GET.get("active", ""),
        "featured": request.GET.get("featured", ""),
        "sort": request.GET.get("sort", "newest"),
    }

    products = get_products(**filters)

    page_obj = paginate_queryset(
        request=request,
        queryset=products,
        per_page=20,
    )

    context = {
        "page_title": _("Products"),
        "page_obj": page_obj,
        "products": page_obj.object_list,
        "filters": filters,
        "categories": get_product_categories(),
        "brands": get_product_brands(),
    }

    return render(
        request,
        "dashboard/products/catalog.html",
        context,
    )
@dashboard_permission_required("products.add_product")
def product_create(request):
    """
    Create a new product.
    """

    if request.method == "POST":
        form = DashboardProductForm(
            request.POST,
            request.FILES,
            can_publish=request.user.has_perm("products.publish_product"),
        )

        if form.is_valid():
            try:
                product = create_product(
                    form=form,
                    user=request.user,
                )
            except ValidationError as error:
                _add_domain_errors(form, error)
            else:
                messages.success(request, _("Product created successfully."))

                return redirect(
                    "dashboard:products:detail",
                    pk=product.pk,
                )

    else:
        form = DashboardProductForm(can_publish=request.user.has_perm("products.publish_product"))

    context = {
        "page_title": _("Create Product"),
        "form": form,
    }

    return render(
        request,
        "dashboard/products/create.html",
        context,
    )
@dashboard_permission_required("products.view_product")
def product_detail(request, pk):
    """
    Display the dashboard product detail page.
    """

    product = get_product(pk=pk)
    if product.product_type_id and product.product_type.code != "tile":
        return redirect("dashboard:products:generic_detail", pk=product.pk)
    detail = get_product_detail_context(product)

    context = {
        "page_title": product.name,
        "product": product,
        "detail": detail,
        # Existing detail components consume these values directly.
        "statistics": detail.statistics,
        "inventory": detail.inventory if request.user.has_perm("products.change_inventory") else None,
        "recent_orders": detail.recent_orders if request.user.has_perm("orders.view_order") else (),
        "recent_reviews": detail.recent_reviews,
        "can_change_product": request.user.has_perm("products.change_product"),
        "can_add_variant": request.user.has_perm("products.add_productvariant"),
        "can_view_pricing": request.user.has_perm("products.change_pricing"),
        "can_view_inventory": request.user.has_perm("products.change_inventory"),
        "can_view_recent_orders": request.user.has_perm("orders.view_order"),
    }

    return render(
        request,
        "dashboard/products/detail.html",
        context,
    )

@dashboard_permission_required("products.change_product")
def product_edit(request, pk):
    """
    Edit an existing product.
    """

    product = get_product(pk=pk)
    if product.product_type_id and product.product_type.code != "tile":
        return redirect("dashboard:products:generic_edit", pk=product.pk)

    if request.method == "POST":
        form = DashboardProductForm(
            request.POST,
            request.FILES,
            instance=product,
            can_publish=request.user.has_perm("products.publish_product"),
        )

        if form.is_valid():
            try:
                update_product(
                    product=product,
                    form=form,
                    user=request.user,
                )
            except ValidationError as error:
                _add_domain_errors(form, error)
            else:
                messages.success(request, _("Product updated successfully."))

                return redirect(
                    "dashboard:products:detail",
                    pk=product.pk,
                )

    else:
        form = DashboardProductForm(
            instance=product,
            can_publish=request.user.has_perm("products.publish_product"),
        )

    context = {
        "page_title": _("Edit %(name)s") % {"name": product.name},
        "product": product,
        "form": form,
    }

    return render(
        request,
        "dashboard/products/edit.html",
        context,
    )


@dashboard_permission_required("products.change_product", "products.add_productimage")
@require_POST
def product_gallery_upload(request, pk):
    product = get_product(pk=pk)
    images = request.FILES.getlist("gallery_images")
    if images:
        try:
            images = validate_raster_images(images)
        except ValidationError as error:
            messages.error(request, error.messages[0])
        else:
            add_product_gallery_images(product=product, files=images)
            messages.success(request, ngettext("Added %(count)d gallery image.", "Added %(count)d gallery images.", len(images)) % {"count": len(images)})
    else:
        messages.error(request, _("Choose at least one image to upload."))
    return redirect("dashboard:products:detail", pk=product.pk)


@dashboard_permission_required("products.change_product", "products.delete_productimage")
@require_POST
def product_gallery_delete(request, pk, image_pk):
    product = get_product(pk=pk)
    image = ProductImage.objects.filter(pk=image_pk, product=product).first()
    if image:
        image.image.delete(save=False)
        image.delete()
        messages.success(request, _("Gallery image removed."))
    return redirect("dashboard:products:detail", pk=product.pk)
