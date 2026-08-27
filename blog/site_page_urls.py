from django.urls import path

from .models import SitePageKey
from .views import site_page_detail


app_name = "sitepages"

urlpatterns = [
    path("about/", site_page_detail, {"key": SitePageKey.ABOUT}, name="about"),
    path("privacy/", site_page_detail, {"key": SitePageKey.PRIVACY}, name="privacy"),
    path("terms/", site_page_detail, {"key": SitePageKey.TERMS}, name="terms"),
    path(
        "shipping-delivery/",
        site_page_detail,
        {"key": SitePageKey.SHIPPING_DELIVERY},
        name="shipping_delivery",
    ),
    path(
        "returns-refunds/",
        site_page_detail,
        {"key": SitePageKey.RETURNS_REFUNDS},
        name="returns_refunds",
    ),
    path(
        "payment-information/",
        site_page_detail,
        {"key": SitePageKey.PAYMENT_INFORMATION},
        name="payment_information",
    ),
]
