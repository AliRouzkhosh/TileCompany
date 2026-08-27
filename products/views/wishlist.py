############### products/views/wishlist.py #################

from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language, pgettext
from django.views.decorators.http import require_POST

from products.models import Product, Wishlist
from products.selectors import get_wishlist_items
from products.catalog_presentation import prepare_product_card_presentations
from products.storefront import get_storefront_currency



@login_required
@require_POST
def wishlist_toggle(request, product_id):

    product = get_object_or_404(Product, id=product_id)
    existing = Wishlist.objects.filter(user=request.user, product=product).first()

    if existing is None:
        if not product.is_active:
            # A stale/unpublished Product can only be removed by its owner;
            # it must never become addable through a forged request.
            raise Http404
        Wishlist.objects.create(user=request.user, product=product)
        in_wishlist = True
        message = pgettext("wishlist message", "Added to wishlist.")
    else:
        existing.delete()
        in_wishlist = False
        message = pgettext("wishlist message", "Removed from wishlist.")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "success": True,
            "in_wishlist": in_wishlist,
            "message": message,
            "wishlist_count": Wishlist.objects.filter(
                user=request.user,
            ).count(),
        })

    next_url = (
        request.POST.get("next")
        or request.META.get("HTTP_REFERER")
    )

    if not url_has_allowed_host_and_scheme(
        next_url or "", allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = None
    return redirect(next_url or "products:home")

@login_required
def wishlist_view(request):

    currency = get_storefront_currency()
    paginator = Paginator(get_wishlist_items(user=request.user, currency=currency), 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    items = list(page_obj.object_list)
    prepare_product_card_presentations(
        items,
        language=get_language(),
        currency=currency,
    )

    return render(
        request,
        "products/wishlist.html",
        {
            "items": items,
            "page_obj": page_obj,
            "storefront_currency": currency,
            "breadcrumbs": [
                {
                    "label": pgettext("account breadcrumb", "My Account"),
                    "url": reverse("accounts:overview"),
                },
                {
                    "label": pgettext("wishlist breadcrumb", "Wishlist"),
                    "url": None,
                }
            ]
        },

    )
