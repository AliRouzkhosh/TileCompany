from django.urls import path

from dashboard.views import catalog_schema as views


app_name = "schema"

urlpatterns = [
    path("", views.schema_home, name="home"),
    path("product-types/", views.product_type_list, name="product_type_list"),
    path("product-types/new/", views.product_type_edit, name="product_type_create"),
    path("product-types/<int:pk>/", views.product_type_detail, name="product_type_detail"),
    path("product-types/<int:pk>/edit/", views.product_type_edit, name="product_type_edit"),
    path("product-types/<int:product_type_pk>/bindings/new/", views.binding_edit, name="binding_create"),
    path("product-types/<int:product_type_pk>/bindings/<int:pk>/edit/", views.binding_edit, name="binding_edit"),
    path("bindings/<int:binding_pk>/memberships/new/", views.membership_edit, name="membership_create"),
    path("bindings/<int:binding_pk>/memberships/<int:pk>/edit/", views.membership_edit, name="membership_edit"),
    path("memberships/<int:pk>/delete/", views.membership_delete, name="membership_delete"),
    path("bindings/<int:pk>/unrestricted/", views.binding_unrestricted, name="binding_unrestricted"),
    path("attributes/", views.definition_list, name="definition_list"),
    path("attributes/new/", views.definition_edit, name="definition_create"),
    path("attributes/<int:pk>/", views.definition_detail, name="definition_detail"),
    path("attributes/<int:pk>/edit/", views.definition_edit, name="definition_edit"),
    path("attributes/<int:definition_pk>/options/new/", views.option_edit, name="option_create"),
    path("attributes/<int:definition_pk>/options/<int:pk>/edit/", views.option_edit, name="option_edit"),
    path("categories/", views.category_list, name="category_list"),
    path("categories/new/", views.category_edit, name="category_create"),
    path("categories/<int:pk>/edit/", views.category_edit, name="category_edit"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
]

