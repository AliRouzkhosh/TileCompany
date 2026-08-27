from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from dashboard.forms import catalog_term_form
from dashboard.permissions import dashboard_permission_required
from products.models import Brand, Category, Color, Design, Finish, Material, Size, Usage
from products.catalog_compat import (
    TAXONOMY_CODE_BY_MODEL,
    delete_tile_catalog_term,
    save_tile_catalog_term,
)
from dashboard.services_catalog_schema import delete_category, save_category


CATALOG_TYPES = {
    "categories": {"label": _lazy("Categories"), "singular": _lazy("Category"), "model": Category, "relation": "categorized_products"},
    "brands": {"label": _lazy("Brands"), "singular": _lazy("Brand"), "model": Brand, "relation": "products"},
    "designs": {"label": _lazy("Designs"), "singular": _lazy("Design"), "model": Design, "relation": "products"},
    "usages": {"label": _lazy("Usages"), "singular": _lazy("Usage"), "model": Usage, "relation": "products"},
    "materials": {"label": _lazy("Materials"), "singular": _lazy("Material"), "model": Material, "relation": "products"},
    "colors": {"label": _lazy("Colors"), "singular": _lazy("Color"), "model": Color, "relation": "variants"},
    "sizes": {"label": _lazy("Sizes"), "singular": _lazy("Size"), "model": Size, "relation": "variants"},
    "finishes": {"label": _lazy("Finishes"), "singular": _lazy("Finish"), "model": Finish, "relation": "variants"},
}


def _config(kind):
    try:
        return CATALOG_TYPES[kind]
    except KeyError:
        raise Http404


def _require_catalog_permission(request, kind, action):
    config = _config(kind)
    permission = f"products.{action}_{config['model']._meta.model_name}"
    if not request.user.has_perm(permission):
        raise PermissionDenied
    return config


def _add_domain_errors(form, error):
    if hasattr(error, "message_dict"):
        for field, field_messages in error.message_dict.items():
            target = field if field in form.fields else None
            for message in field_messages:
                form.add_error(target, message)
    else:
        for message in error.messages:
            form.add_error(None, message)


@dashboard_permission_required(
    "products.view_category", "products.view_brand", "products.view_design", "products.view_usage",
    "products.view_material", "products.view_color", "products.view_size", "products.view_finish",
    any_of=True,
)
def catalog_data_home(request):
    groups = []
    for kind, config in CATALOG_TYPES.items():
        groups.append({"kind": kind, "label": config["label"], "count": config["model"].objects.count()})
    return render(request, "dashboard/products/catalog_data_home.html", {"groups": groups})


@dashboard_permission_required(
    "products.view_category", "products.view_brand", "products.view_design", "products.view_usage",
    "products.view_material", "products.view_color", "products.view_size", "products.view_finish",
    any_of=True,
)
def catalog_term_list(request, kind):
    config = _require_catalog_permission(request, kind, "view")
    items = config["model"].objects.annotate(usage_count=Count(config["relation"])).order_by("name")
    return render(request, "dashboard/products/catalog_term_list.html", {"config": config, "kind": kind, "items": items})


@login_required
def catalog_term_edit(request, kind, pk=None):
    config = _require_catalog_permission(request, kind, "change" if pk else "add")
    instance = get_object_or_404(config["model"], pk=pk) if pk else None
    form = catalog_term_form(config["model"])(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        try:
            if config["model"] in TAXONOMY_CODE_BY_MODEL:
                save_tile_catalog_term(form)
            elif config["model"] is Category:
                save_category(form)
            else:
                form.save()
        except ValidationError as error:
            _add_domain_errors(form, error)
        else:
            messages.success(request, _("%(term)s saved.") % {"term": config["singular"]})
            return redirect("dashboard:products:catalog_term_list", kind=kind)
    return render(request, "dashboard/products/catalog_term_editor.html", {"form": form, "config": config, "kind": kind, "item": instance})


@login_required
def catalog_term_delete(request, kind, pk):
    config = _require_catalog_permission(request, kind, "delete")
    item = get_object_or_404(config["model"], pk=pk)
    usage_count = getattr(item, config["relation"]).count()
    if request.method == "POST":
        if usage_count:
            messages.error(
                request,
                _("This %(term)s is used by %(count)s catalog record(s) and cannot be deleted.")
                % {"term": str(config["singular"]).lower(), "count": usage_count},
            )
        else:
            try:
                if config["model"] in TAXONOMY_CODE_BY_MODEL:
                    delete_tile_catalog_term(item)
                elif config["model"] is Category:
                    delete_category(item)
                else:
                    item.delete()
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
            else:
                messages.success(request, _("%(term)s deleted.") % {"term": config["singular"]})
        return redirect("dashboard:products:catalog_term_list", kind=kind)
    return render(request, "dashboard/products/catalog_term_delete.html", {"config": config, "kind": kind, "item": item, "usage_count": usage_count})
