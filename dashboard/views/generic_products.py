from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from dashboard.forms_generic_product import GenericProductForm
from dashboard.permissions import dashboard_permission_required
from dashboard.selectors.generic_products import (
    get_active_generic_product_types,
    get_generic_product,
    load_generic_product_form_context,
)
from dashboard.services_generic_product import create_generic_product, update_generic_product
from products.catalog_read import read_product_detail
from products.models import ProductType
from products.publication_readiness import evaluate_product_publication_readiness
from products.publication_services import (
    PublicationBlockedError,
    publish_product,
    unpublish_product,
)


def _add_errors(form, error):
    if hasattr(error, "message_dict"):
        for field, values in error.message_dict.items():
            target = field if field in form.fields else None
            for value in values:
                form.add_error(target, value)
    else:
        for value in error.messages:
            form.add_error(None, value)


@dashboard_permission_required("products.view_product")
def generic_product_type_select(request):
    return render(request, "dashboard/generic_products/select_type.html", {
        "product_types": get_active_generic_product_types(),
    })


@dashboard_permission_required("products.add_product")
def generic_product_create(request, product_type_pk):
    product_type = get_object_or_404(ProductType, pk=product_type_pk)
    if product_type.code == "tile":
        messages.error(request, _("Tile Products must use the existing Tile Product editor."))
        return redirect("dashboard:products:create")
    if not product_type.is_active:
        messages.error(request, _("Inactive ProductTypes cannot be used for new Products."))
        return redirect("dashboard:products:generic_type_select")
    schema_context = load_generic_product_form_context(product_type)
    form = GenericProductForm(
        request.POST or None, request.FILES or None,
        product_type=product_type, schema_context=schema_context,
    )
    if request.method == "POST" and form.is_valid():
        try:
            product = create_generic_product(product_type=product_type, form=form, user=request.user)
        except ValidationError as error:
            _add_errors(form, error)
        except PublicationBlockedError as error:
            for issue in error.issues:
                form.add_error(None, issue.message)
        else:
            messages.success(request, _("Generic Product created as an inactive draft."))
            return redirect("dashboard:products:generic_detail", pk=product.pk)
    return render(request, "dashboard/generic_products/form.html", {
        "form": form, "product_type": product_type, "product": None,
        "title": _("Create %(type)s Product") % {"type": product_type.name},
    })


@dashboard_permission_required("products.change_product")
def generic_product_edit(request, pk):
    product = get_generic_product(pk)
    if not product.product_type_id or product.product_type.code == "tile":
        messages.error(request, _("Tile Products must use the existing Tile Product editor."))
        return redirect("dashboard:products:edit", pk=product.pk)
    schema_context = load_generic_product_form_context(product.product_type, product=product)
    form = GenericProductForm(
        request.POST or None, request.FILES or None, instance=product,
        product_type=product.product_type, schema_context=schema_context,
    )
    if request.method == "POST" and form.is_valid():
        try:
            product = update_generic_product(product=product, form=form, user=request.user)
        except ValidationError as error:
            _add_errors(form, error)
        except PublicationBlockedError as error:
            for issue in error.issues:
                form.add_error(None, issue.message)
        else:
            messages.success(request, _("Generic Product updated."))
            return redirect("dashboard:products:generic_detail", pk=product.pk)
    return render(request, "dashboard/generic_products/form.html", {
        "form": form, "product_type": product.product_type, "product": product,
        "title": _("Edit %(name)s") % {"name": product.name},
    })


@dashboard_permission_required("products.view_product")
def generic_product_detail(request, pk):
    product = get_generic_product(pk)
    if not product.product_type_id or product.product_type.code == "tile":
        return redirect("dashboard:products:detail", pk=product.pk)
    detail = read_product_detail(product)
    readiness = evaluate_product_publication_readiness(product)
    return render(request, "dashboard/generic_products/detail.html", {
        "product": product, "attributes": detail.product_attributes, "generic_detail": detail,
        "readiness": readiness,
    })


def _transition_error_messages(request, error):
    for issue in error.issues:
        messages.error(request, issue.message)


@dashboard_permission_required("products.publish_product")
@require_POST
def generic_product_publish(request, pk):
    product = get_generic_product(pk)
    try:
        result = publish_product(product, user=request.user)
    except PublicationBlockedError as error:
        _transition_error_messages(request, error)
    else:
        messages.success(request, _("Product published."))
        for warning in result.warnings:
            messages.warning(request, warning.message)
    return redirect("dashboard:products:generic_detail", pk=pk)


@dashboard_permission_required("products.publish_product")
@require_POST
def generic_product_unpublish(request, pk):
    product = get_generic_product(pk)
    unpublish_product(product, user=request.user)
    messages.success(request, _("Product unpublished."))
    return redirect("dashboard:products:generic_detail", pk=pk)
