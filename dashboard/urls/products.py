############## dashboard/urls/products.py ############
from django.urls import include, path

from dashboard.views.products import (
    product_catalog,
    product_create,
    product_detail,
    product_edit,
    product_gallery_delete,
    product_gallery_upload,
)
from dashboard.views.catalog_data import catalog_data_home, catalog_term_delete, catalog_term_edit, catalog_term_list
from dashboard.views.generic_products import generic_product_create, generic_product_detail, generic_product_edit, generic_product_publish, generic_product_unpublish, generic_product_type_select
from dashboard.views.generic_variants import generic_variant_catalog, generic_variant_create, generic_variant_edit, generic_variant_publish, generic_variant_unpublish
from dashboard.views.inventory_imports import inventory_import, inventory_import_apply, inventory_import_current_xlsx, inventory_import_detail, inventory_import_source_download, inventory_import_template_csv, inventory_import_template_xlsx, inventory_import_upload

app_name = "products"

urlpatterns = [
    path("bulk-inventory-pricing/", inventory_import, name="inventory_import"),
    path("bulk-inventory-pricing/upload/", inventory_import_upload, name="inventory_import_upload"),
    path("bulk-inventory-pricing/<int:pk>/", inventory_import_detail, name="inventory_import_detail"),
    path("bulk-inventory-pricing/<int:pk>/apply/", inventory_import_apply, name="inventory_import_apply"),
    path("bulk-inventory-pricing/<int:pk>/source/", inventory_import_source_download, name="inventory_import_source_download"),
    path("bulk-inventory-pricing/template.xlsx", inventory_import_template_xlsx, name="inventory_import_template_xlsx"),
    path("bulk-inventory-pricing/template.csv", inventory_import_template_csv, name="inventory_import_template_csv"),
    path("bulk-inventory-pricing/current.xlsx", inventory_import_current_xlsx, name="inventory_import_current_xlsx"),
    path("generic/new/", generic_product_type_select, name="generic_type_select"),
    path("generic/new/<int:product_type_pk>/", generic_product_create, name="generic_create"),
    path("generic/<int:product_pk>/variants/", generic_variant_catalog, name="generic_variant_catalog"),
    path("generic/<int:product_pk>/variants/new/", generic_variant_create, name="generic_variant_create"),
    path("generic/<int:product_pk>/variants/<int:variant_pk>/edit/", generic_variant_edit, name="generic_variant_edit"),
    path("generic/<int:product_pk>/variants/<int:variant_pk>/publish/", generic_variant_publish, name="generic_variant_publish"),
    path("generic/<int:product_pk>/variants/<int:variant_pk>/unpublish/", generic_variant_unpublish, name="generic_variant_unpublish"),
    path("generic/<int:pk>/", generic_product_detail, name="generic_detail"),
    path("generic/<int:pk>/edit/", generic_product_edit, name="generic_edit"),
    path("generic/<int:pk>/publish/", generic_product_publish, name="generic_publish"),
    path("generic/<int:pk>/unpublish/", generic_product_unpublish, name="generic_unpublish"),
    path("catalog-data/", catalog_data_home, name="catalog_data"),
    path("catalog-data/<str:kind>/", catalog_term_list, name="catalog_term_list"),
    path("catalog-data/<str:kind>/new/", catalog_term_edit, name="catalog_term_create"),
    path("catalog-data/<str:kind>/<int:pk>/edit/", catalog_term_edit, name="catalog_term_edit"),
    path("catalog-data/<str:kind>/<int:pk>/delete/", catalog_term_delete, name="catalog_term_delete"),
    path(
        "",
        product_catalog,
        name="catalog",
    ),
    path(
        "create/",
        product_create,
        name="create",
    ),
    path(
        "<int:pk>/",
        product_detail,
        name="detail",
    ),
    path(
        "<int:pk>/edit/",
        product_edit,
        name="edit",
    ),
    path("<int:pk>/gallery/upload/", product_gallery_upload, name="gallery_upload"),
    path("<int:pk>/gallery/<int:image_pk>/delete/", product_gallery_delete, name="gallery_delete"),
    path(
        "<int:product_pk>/variants/",
        include("dashboard.urls.variants"),
    ),
]
