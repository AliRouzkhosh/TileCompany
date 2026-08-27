################# dashboard/urls/analytics.py #################
from django.urls import path

from dashboard.views.analytics import analytics, export_customers_csv, export_orders_csv, export_products_csv, export_refunds_csv, export_report_xlsx

urlpatterns = [
    path(
        "analytics/",
        analytics,
        name="analytics",
    ),
    path("analytics/export/orders.csv", export_orders_csv, name="analytics_export_orders_csv"),
    path("analytics/export/refunds.csv", export_refunds_csv, name="analytics_export_refunds_csv"),
    path("analytics/export/products.csv", export_products_csv, name="analytics_export_products_csv"),
    path("analytics/export/customers.csv", export_customers_csv, name="analytics_export_customers_csv"),
    path("analytics/export/report.xlsx", export_report_xlsx, name="analytics_export_report_xlsx"),
]
