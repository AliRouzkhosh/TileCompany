from django.http import HttpResponse
from django.shortcuts import render

from dashboard.permissions import dashboard_permission_required, owner_required
from dashboard.selectors.analytics import get_analytics_context, resolve_analytics_period
from dashboard.selectors.analytics_exports import (
    build_xlsx, write_customers_csv, write_orders_csv, write_products_csv,
    write_refunds_csv,
)


@dashboard_permission_required("dashboard.view_sensitive_analytics")
def analytics(request):
    period = resolve_analytics_period(request.GET)
    context = get_analytics_context(period, include_customer_details=request.user.is_superuser)
    context.update(revenue_chart=context["revenue_series"], orders_chart=context["order_series"])
    return render(request, "dashboard/analytics/index.html", context)


def _csv_response(filename, writer, period):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer(response, period)
    return response


@owner_required
def export_orders_csv(request):
    return _csv_response("tilestore-orders.csv", write_orders_csv, resolve_analytics_period(request.GET))


@owner_required
def export_refunds_csv(request):
    return _csv_response("tilestore-refunds.csv", write_refunds_csv, resolve_analytics_period(request.GET))


@dashboard_permission_required("dashboard.view_sensitive_analytics")
def export_products_csv(request):
    return _csv_response("tilestore-products.csv", write_products_csv, resolve_analytics_period(request.GET))


@owner_required
def export_customers_csv(request):
    return _csv_response("tilestore-customers.csv", write_customers_csv, resolve_analytics_period(request.GET))


@owner_required
def export_report_xlsx(request):
    response = HttpResponse(build_xlsx(resolve_analytics_period(request.GET)), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="tilestore-analytics.xlsx"'
    return response
