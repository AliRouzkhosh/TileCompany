################## products/urls.py ########################
from django.urls import path
from . import views

app_name = "products"

urlpatterns = [
    path("", views.home, name="home"),
    path("shop/", views.product_list, name="product_list"),
    path("shop/category/<slug:slug>/", views.product_list, name="category_list"),
    path("product/<slug:slug>/", views.product_detail, name="product_detail"),
    path("wishlist/", views.wishlist_view, name="wishlist"),
    path("wishlist/toggle/<int:product_id>/", views.wishlist_toggle, name="wishlist_toggle"),
    path( "products/search-suggestions/",views.search_suggestions,name="search_suggestions",),
    path("review/<int:pk>/edit/",views.edit_review,name="edit_review",),
    path("review/<int:pk>/delete/",views.delete_review,name="delete_review",),
    path("review/<int:review_id>/comment/",views.add_comment,name="add_comment",),
    path("comment/<int:pk>/edit/",views.edit_comment,name="edit_comment",),
    path("comment/<int:pk>/delete/",views.delete_comment,name="delete_comment",),
]