################# dashboard/urls/__init__.py #################
from django.urls import include, path

app_name = "dashboard"

urlpatterns = [
    path("", include("dashboard.urls.home")),
    path("", include("dashboard.urls.ckeditor")),
    path("", include("dashboard.urls.orders")),
    path("", include("dashboard.urls.analytics")),
    path("", include("dashboard.urls.moderation")),
    path("products/", include("dashboard.urls.products")),
    path("catalog-schema/", include("dashboard.urls.catalog_schema")),
    path("blog/", include("dashboard.urls.blog")),
    path("support/", include("dashboard.urls.support")),
    path("storefront/", include("dashboard.urls.storefront")),
    path("staff-access/", include("dashboard.urls.staff_access")),
]
