from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import get_language, gettext as _
from django.views.decorators.http import require_POST

from dashboard.forms_imports import InventoryPriceImportUploadForm
from dashboard.permissions import dashboard_permission_required
from products.inventory_price_exports import build_inventory_price_csv_template, build_inventory_price_xlsx
from products.inventory_price_imports import ImportSourceError, apply_inventory_price_import, create_inventory_price_import_preview
from products.models import InventoryPriceImportBatch, InventoryPriceImportRow, ProductOperationalChange


def _batch_or_404(pk):
    return get_object_or_404(InventoryPriceImportBatch.objects.select_related("actor", "applied_by"), pk=pk)


@dashboard_permission_required("products.run_inventory_price_import")
def inventory_import(request):
    return render(request, "dashboard/products/inventory_import/index.html", {
        "form": InventoryPriceImportUploadForm(),
        "batches": InventoryPriceImportBatch.objects.select_related("actor", "applied_by")[:20],
    })


@dashboard_permission_required("products.run_inventory_price_import")
def inventory_import_detail(request, pk):
    batch = _batch_or_404(pk)
    show = request.GET.get("show", "all")
    statuses = {
        "changes": InventoryPriceImportRow.Status.CHANGED,
        "errors": InventoryPriceImportRow.Status.ERROR,
        "unchanged": InventoryPriceImportRow.Status.UNCHANGED,
    }
    rows = batch.rows.select_related("variant__product").order_by("row_number")
    if show in statuses:
        rows = rows.filter(row_status=statuses[show])
    elif show == "warnings":
        rows = [row for row in rows if row.warnings]
    event_count = ProductOperationalChange.objects.filter(import_batch=batch).count()
    return render(request, "dashboard/products/inventory_import/detail.html", {
        "batch": batch, "rows": rows, "show": show, "event_count": event_count,
        "can_apply": request.user.has_perm("products.apply_inventory_price_import"),
    })


@dashboard_permission_required("products.run_inventory_price_import")
@require_POST
def inventory_import_upload(request):
    form = InventoryPriceImportUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "dashboard/products/inventory_import/index.html", {"form": form, "batches": InventoryPriceImportBatch.objects.select_related("actor", "applied_by")[:20]}, status=400)
    try:
        batch = create_inventory_price_import_preview(actor=request.user, uploaded_file=form.cleaned_data["source"])
    except ImportSourceError as error:
        form.add_error("source", error.message)
        return render(request, "dashboard/products/inventory_import/index.html", {"form": form, "batches": InventoryPriceImportBatch.objects.select_related("actor", "applied_by")[:20]}, status=400)
    return redirect("dashboard:products:inventory_import_detail", pk=batch.pk)


@dashboard_permission_required("products.apply_inventory_price_import")
@require_POST
def inventory_import_apply(request, pk):
    batch = _batch_or_404(pk)
    try:
        result = apply_inventory_price_import(actor=request.user, batch_id=batch.pk)
    except ValueError:
        messages.error(request, _("This import batch cannot be applied."))
    else:
        if result.get("stale"):
            messages.warning(request, _("This preview is no longer current. Upload fresh data and preview again."))
        elif result.get("no_changes"):
            messages.info(request, _("No changes were detected."))
        elif result.get("already_applied"):
            messages.info(request, _("This batch was already applied."))
        else:
            messages.success(request, _("Inventory and pricing changes were applied successfully."))
    return redirect("dashboard:products:inventory_import_detail", pk=batch.pk)


def _download(name, payload, content_type):
    response = HttpResponse(payload, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["Cache-Control"] = "private, no-store"
    return response


@dashboard_permission_required("products.run_inventory_price_import")
def inventory_import_template_xlsx(request):
    return _download("inventory-price-template.xlsx", build_inventory_price_xlsx(language=get_language() or "en", include_current=False), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@dashboard_permission_required("products.run_inventory_price_import")
def inventory_import_template_csv(request):
    return _download("inventory-price-template.csv", build_inventory_price_csv_template(), "text/csv; charset=utf-8")


@dashboard_permission_required("products.run_inventory_price_import")
def inventory_import_current_xlsx(request):
    return _download("current-inventory-pricing.xlsx", build_inventory_price_xlsx(language=get_language() or "en", include_current=True), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@dashboard_permission_required("products.run_inventory_price_import")
def inventory_import_source_download(request, pk):
    batch = _batch_or_404(pk)
    if not batch.source_file or not batch.source_file.storage.exists(batch.source_file.name):
        raise Http404
    response = FileResponse(batch.source_file.open("rb"), as_attachment=True, filename=batch.original_filename)
    response["Cache-Control"] = "private, no-store"
    return response
