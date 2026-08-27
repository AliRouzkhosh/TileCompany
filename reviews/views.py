# reviews/views.py

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib import messages
from django.utils.translation import gettext as _
from TileCompany.htmx import is_htmx
from django.views.decorators.http import require_POST
from django.db import IntegrityError, transaction

from products.models import Product
from orders.models import OrderItem

from .forms import ReviewForm
from .models import Review
from .services import user_can_review_product
from .services import (
    user_can_comment_review,
    user_can_reply_comment,
)


def _review_region(request, product, *, form=None):
    from products.selectors import get_product_review_queryset, get_review_context

    context = get_review_context(user=request.user, product=product)
    if form is not None:
        context["form"] = form
    return render(request, "products/components/reviews/review_region.html", {
        "product": product,
        "reviews": get_product_review_queryset(product, request.user),
        **context,
    })


@login_required
@require_POST
def create_review(request, product_id):
    """
    Submit a new product review.
    Returns JSON for AJAX requests.
    """

    product = get_object_or_404(
        Product,
        id=product_id,
        is_active=True,
    )

    # Must have purchased the product
    has_purchased = OrderItem.objects.filter(
        order__user=request.user,
        order__status="delivered",
        product=product,
    ).exists()

    if not has_purchased:
        if is_htmx(request):
            return _review_region(request, product)
        messages.error(request, _("Only customers who purchased this product can leave a review."))
        return redirect(f"{product.get_absolute_url()}#reviews")
        return JsonResponse(
            {
                "success": False,
                "message": "Only customers who purchased this product can leave a review.",
            },
            status=403,
        )

    # One review per product
    if Review.objects.filter(
        user=request.user,
        product=product,
    ).exists():
        if is_htmx(request):
            return _review_region(request, product)
        messages.error(request, _("You have already reviewed this product."))
        return redirect(f"{product.get_absolute_url()}#reviews")
        return JsonResponse(
            {
                "success": False,
                "message": "You have already reviewed this product.",
            },
            status=400,
        )

    # Pending review check
    if not user_can_review_product(request.user, product):
        if is_htmx(request):
            return _review_region(request, product)
        messages.error(request, _("Your previous review is still awaiting approval."))
        return redirect(f"{product.get_absolute_url()}#reviews")
        return JsonResponse(
            {
                "success": False,
                "message": "Your previous review is still awaiting approval.",
            },
            status=400,
        )

    form = ReviewForm(request.POST)

    if not form.is_valid():
        if is_htmx(request):
            return _review_region(request, product, form=form)
        messages.error(request, _("Please correct the errors in your review."))
        return redirect(f"{product.get_absolute_url()}#reviews")
        return JsonResponse(
            {
                "success": False,
                "errors": form.errors,
            },
            status=400,
        )

    try:
        with transaction.atomic():
            review = form.save(commit=False)
            review.product = product
            review.user = request.user
            review.save()
    except IntegrityError:
        if is_htmx(request):
            return _review_region(request, product)
        messages.error(request, _("You have already reviewed this product."))
        return redirect(f"{product.get_absolute_url()}#reviews")
        return JsonResponse(
            {"success": False, "message": "You have already reviewed this product."},
            status=400,
        )

    if is_htmx(request):
        return _review_region(request, product)

    messages.success(request, _("Thanks! Your review was submitted for approval."))
    return redirect(f"{product.get_absolute_url()}#reviews")

    return JsonResponse(
        {
            "success": True,
            "message": "Thanks! Your review was submitted for approval.",
        }
    )
