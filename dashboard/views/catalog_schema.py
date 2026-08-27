from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from dashboard.catalog_schema_policy import (
    binding_impact,
    option_impact,
    product_type_impact,
)
from dashboard.forms_catalog_schema import (
    AttributeDefinitionForm,
    AttributeOptionForm,
    CategorySchemaForm,
    ProductTypeAttributeForm,
    ProductTypeAttributeOptionForm,
    ProductTypeForm,
)
from dashboard.permissions import owner_required
from dashboard.selectors.catalog_schema import (
    get_category_tree,
    get_definition_options,
    get_definitions,
    get_product_type_schema,
    get_product_types,
)
from dashboard.services_catalog_schema import (
    create_attribute_definition,
    create_attribute_option,
    create_binding,
    create_membership,
    create_product_type,
    delete_category,
    delete_membership,
    save_category,
    set_binding_unrestricted,
    update_attribute_definition,
    update_attribute_option,
    update_binding,
    update_membership,
    update_product_type,
)
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Category,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
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


@owner_required
def schema_home(request):
    return render(request, "dashboard/schema/home.html", {
        "product_type_count": ProductType.objects.count(),
        "definition_count": AttributeDefinition.objects.count(),
        "option_count": AttributeOption.objects.count(),
        "category_count": Category.objects.count(),
    })


@owner_required
def product_type_list(request):
    return render(request, "dashboard/schema/product_types.html", {"items": get_product_types()})


@owner_required
def product_type_edit(request, pk=None):
    instance = get_object_or_404(ProductType, pk=pk) if pk else None
    if instance and instance.code == "tile":
        messages.error(request, _("Tile metadata is read-only in the generic schema editor."))
        return redirect("dashboard:schema:product_type_detail", pk=instance.pk)
    form = ProductTypeForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        try:
            saved = update_product_type(instance, form) if instance else create_product_type(form)
        except ValidationError as error:
            _add_errors(form, error)
        else:
            messages.success(request, _("Product type saved."))
            return redirect("dashboard:schema:product_type_detail", pk=saved.pk)
    return render(request, "dashboard/schema/form.html", {
        "form": form, "title": _("Edit Product Type") if instance else _("New Product Type"),
        "cancel_url": "dashboard:schema:product_type_list",
    })


@owner_required
def product_type_detail(request, pk):
    product_type = get_object_or_404(ProductType, pk=pk)
    return render(request, "dashboard/schema/product_type_detail.html", {
        "item": product_type,
        "schema": get_product_type_schema(product_type),
        "impact": product_type_impact(product_type),
        "is_protected": product_type.code == "tile",
    })


@owner_required
def definition_list(request):
    return render(request, "dashboard/schema/definitions.html", {"items": get_definitions()})


@owner_required
def definition_edit(request, pk=None):
    instance = get_object_or_404(AttributeDefinition, pk=pk) if pk else None
    form = AttributeDefinitionForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        try:
            saved = update_attribute_definition(instance, form) if instance else create_attribute_definition(form)
        except ValidationError as error:
            _add_errors(form, error)
        else:
            messages.success(request, _("Attribute definition saved."))
            return redirect("dashboard:schema:definition_detail", pk=saved.pk)
    return render(request, "dashboard/schema/form.html", {
        "form": form, "title": _("Edit Attribute") if instance else _("New Attribute"),
        "cancel_url": "dashboard:schema:definition_list",
    })


@owner_required
def definition_detail(request, pk):
    definition = get_object_or_404(AttributeDefinition, pk=pk)
    return render(request, "dashboard/schema/definition_detail.html", {
        "item": definition,
        "options": get_definition_options(definition),
        "tile_protected": definition.product_type_bindings.filter(product_type__code="tile").exists(),
    })


@owner_required
def option_edit(request, definition_pk, pk=None):
    definition = get_object_or_404(AttributeDefinition, pk=definition_pk)
    instance = get_object_or_404(AttributeOption, definition=definition, pk=pk) if pk else None
    form = AttributeOptionForm(request.POST or None, instance=instance, definition=definition)
    if request.method == "POST" and form.is_valid():
        try:
            saved = update_attribute_option(instance, form) if instance else create_attribute_option(definition, form)
        except ValidationError as error:
            _add_errors(form, error)
        else:
            messages.success(request, _("Attribute option saved."))
            return redirect("dashboard:schema:definition_detail", pk=saved.definition_id)
    return render(request, "dashboard/schema/form.html", {
        "form": form, "title": _("Edit Option") if instance else _("New Option"),
        "cancel_object": definition,
        "option_impact": option_impact(instance) if instance else None,
    })


@owner_required
def binding_edit(request, product_type_pk, pk=None):
    product_type = get_object_or_404(ProductType, pk=product_type_pk)
    instance = get_object_or_404(ProductTypeAttribute, product_type=product_type, pk=pk) if pk else None
    form = ProductTypeAttributeForm(request.POST or None, instance=instance, product_type=product_type)
    if request.method == "POST" and form.is_valid():
        try:
            saved = update_binding(instance, form) if instance else create_binding(product_type, form)
        except ValidationError as error:
            _add_errors(form, error)
        else:
            messages.success(request, _("Schema binding saved."))
            return redirect("dashboard:schema:product_type_detail", pk=saved.product_type_id)
    return render(request, "dashboard/schema/form.html", {
        "form": form, "title": _("Edit Schema Attribute") if instance else _("Add Schema Attribute"),
        "product_type": product_type,
        "impact": binding_impact(instance) if instance else product_type_impact(product_type),
    })


@owner_required
def membership_edit(request, binding_pk, pk=None):
    binding = get_object_or_404(ProductTypeAttribute.objects.select_related("product_type", "definition"), pk=binding_pk)
    instance = get_object_or_404(ProductTypeAttributeOption, binding=binding, pk=pk) if pk else None
    form = ProductTypeAttributeOptionForm(request.POST or None, instance=instance, binding=binding)
    if request.method == "POST" and form.is_valid():
        try:
            saved = update_membership(instance, form) if instance else create_membership(binding, form)
        except ValidationError as error:
            _add_errors(form, error)
        else:
            messages.success(request, _("Allowed option saved."))
            return redirect("dashboard:schema:product_type_detail", pk=saved.binding.product_type_id)
    return render(request, "dashboard/schema/form.html", {
        "form": form, "title": _("Edit Allowed Option") if instance else _("Add Allowed Option"),
        "product_type": binding.product_type, "binding": binding,
    })


@owner_required
def membership_delete(request, pk):
    membership = get_object_or_404(
        ProductTypeAttributeOption.objects.select_related("binding__product_type", "option"), pk=pk
    )
    if request.method == "POST":
        try:
            delete_membership(membership)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, _("Allowed option removed."))
        return redirect("dashboard:schema:product_type_detail", pk=membership.binding.product_type_id)
    return render(request, "dashboard/schema/confirm_delete.html", {"item": membership})


@owner_required
def binding_unrestricted(request, pk):
    binding = get_object_or_404(ProductTypeAttribute.objects.select_related("product_type"), pk=pk)
    if request.method == "POST":
        try:
            set_binding_unrestricted(binding, confirmed=request.POST.get("confirm") == "yes")
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, _("Unrestricted option mode enabled explicitly."))
    return redirect("dashboard:schema:product_type_detail", pk=binding.product_type_id)


@owner_required
def category_list(request):
    return render(request, "dashboard/schema/categories.html", {"items": get_category_tree()})


@owner_required
def category_edit(request, pk=None):
    instance = get_object_or_404(Category, pk=pk) if pk else None
    form = CategorySchemaForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        try:
            saved = save_category(form)
        except ValidationError as error:
            _add_errors(form, error)
        else:
            messages.success(request, _("Category saved."))
            return redirect("dashboard:schema:category_list")
    return render(request, "dashboard/schema/form.html", {
        "form": form, "title": _("Edit Category") if instance else _("New Category"),
        "cancel_url": "dashboard:schema:category_list",
    })


@owner_required
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == "POST":
        try:
            delete_category(category)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, _("Category deleted."))
        return redirect("dashboard:schema:category_list")
    return render(request, "dashboard/schema/confirm_delete.html", {"item": category})

