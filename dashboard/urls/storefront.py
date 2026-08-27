from django.urls import path

from dashboard.views.storefront import media, update_category, update_editorial, update_existing_merchandising_media, update_hero, update_merchandising_media


app_name = "storefront"

urlpatterns = [
    path("media/", media, name="media"),
    path("media/hero/", update_hero, name="update_hero"),
    path("media/editorial/", update_editorial, name="update_editorial"),
    path("media/categories/<int:pk>/", update_category, name="update_category"),
    path("media/discovery/", update_merchandising_media, name="update_merchandising_media"),
    path("media/discovery/<int:pk>/", update_existing_merchandising_media, name="update_existing_merchandising_media"),
]
