from django.urls import path

from dashboard.views.variants import variant_catalog, variant_create, variant_edit, variant_gallery_delete


app_name = "variants"

urlpatterns = [
    path("", variant_catalog, name="catalog"),
    path("add/", variant_create, name="create"),
    path("<int:variant_pk>/edit/", variant_edit, name="edit"),
    path("<int:variant_pk>/gallery/<int:image_pk>/delete/", variant_gallery_delete, name="gallery_delete"),
]
