############# orders/urls.py ##############3
from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    # ======================================================
    # Cart
    # ======================================================

    path(
        "cart/",
        views.cart_detail,
        name="cart_detail",
    ),
    path(
        "cart/add-variant/",
        views.cart_add_variant,
        name="cart_add_variant",
    ),
    path(
        "cart/update/<str:cart_key>/",
        views.cart_update,
        name="cart_update",
    ),
    path(
        "cart/remove/<str:cart_key>/",
        views.cart_remove,
        name="cart_remove",
    ),

    # ======================================================
    # Checkout
    # ======================================================

    path(
        "checkout/shipping/",
        views.shipping,
        name="checkout_shipping",
    ),
    path(
        "checkout/payment/",
        views.payment,
        name="checkout_payment",
    ),
    path(
        "checkout/review/",
        views.review,
        name="checkout_review",
    ),

    # ======================================================
    # Payments
    # ======================================================

    path(
        "payment/<int:order_id>/",
        views.payment_start,
        name="payment_start",
    ),
    path(
        "payment/<int:order_id>/callback/",
        views.payment_callback,
        name="payment_callback",
    ),
    path(
        "payment/<int:order_id>/mock/",
        views.mock_payment,
        name="mock_payment",
    ),
    path(
        "payment/<int:order_id>/local-simulator/",
        views.local_payment_simulator,
        name="local_payment_simulator",
    ),
    path(
        "payment/<int:order_id>/result/",
        views.order_result,
        name="order_result",
    ),

    # ======================================================
    # Orders
    # ======================================================

    path(
        "my-orders/",
        views.order_list,
        name="order_list",
    ),
    path(
        "my-orders/<int:order_id>/",
        views.order_detail,
        name="order_detail",
    ),
    path(
        "my-orders/<int:order_id>/cancel/",
        views.order_cancel,
        name="order_cancel",
    ),
    path("refund-attachments/<int:attachment_id>/download/", views.refund_attachment_download, name="refund_attachment_download"),
]
