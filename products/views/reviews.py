############# products/views/reviews.py ##########################
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import F, Sum, Min, Max, Exists, OuterRef
from urllib.parse import urlencode
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.db.models import Prefetch
from django.utils.translation import gettext as _
from TileCompany.htmx import is_htmx

from products.search.normalizer import normalize_size
from products.search.categories import grouped_suggestions
from products.search.suggestions import product_suggestions
from products.models import Product, Category, ProductView, Wishlist, ProductVariant
from reviews.models import Review, ReviewComment
from reviews.forms import ReviewForm, ReviewCommentForm
from reviews.services import (
    user_can_review_product,
    user_can_comment_review,
    user_can_reply_comment
)
from orders.models import OrderItem
from blog.models import Article
from products.search import search_products
from blog.selectors import get_latest_articles
from products.selectors import get_product_review_queryset, get_review_context
def _review_region(request, product, *, form=None):
    """Render the Product Detail reviews workspace without the global shell."""
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
def edit_review(request, pk):
    review = get_object_or_404(
        Review,
        pk=pk,
        user=request.user,
    )

    form = ReviewForm(
        request.POST,
        instance=review,
    )

    if not form.is_valid():
        if is_htmx(request):
            return _review_region(request, review.product, form=form)
        messages.error(request, _("Please correct the errors in your review."))
        return redirect(f"{review.product.get_absolute_url()}#reviews")

    review = form.save(commit=False)
    review.is_approved = False
    review.save()

    if is_htmx(request):
        return _review_region(request, review.product)
    messages.success(request, _("Your edited review now requires moderation again."))
    return redirect(f"{review.product.get_absolute_url()}#reviews")


@login_required
@require_POST
def delete_review(request, pk):
    review = get_object_or_404(
        Review,
        pk=pk,
        user=request.user,
    )

    product = review.product
    review.delete()

    if is_htmx(request):
        return _review_region(request, product)
    messages.success(request, _("Review deleted."))
    return redirect(f"{product.get_absolute_url()}#reviews")


@login_required
@require_POST
def add_comment(request, review_id):
    review = get_object_or_404(
        Review,
        pk=review_id,
        is_approved=True,
    )

    parent = None

    parent_id = request.POST.get("parent")

    # ----------------------------------------------------------
    # Reply
    # ----------------------------------------------------------

    if parent_id:

        parent = get_object_or_404(
            ReviewComment,
            pk=parent_id,
            review=review,
            is_approved=True,
        )

        if parent.parent_id:
            if is_htmx(request):
                return _review_region(request, review.product)
            messages.error(request, _("Replies may only be made to a top-level comment."))
            return redirect(f"{review.product.get_absolute_url()}#reviews")
            return JsonResponse(
                {"success": False, "message": _("Replies may only be made to a top-level comment.")},
                status=400,
            )

        if not user_can_reply_comment(
            request.user,
            parent,
        ):
            if is_htmx(request):
                return _review_region(request, review.product)
            messages.error(request, _("Your previous reply is still awaiting approval."))
            return redirect(f"{review.product.get_absolute_url()}#reviews")
            return JsonResponse(
                {
                    "success": False,
                    "message": (
                        _("Your previous reply is still awaiting approval.")
                    ),
                },
                status=400,
            )

    # ----------------------------------------------------------
    # Top-level comment
    # ----------------------------------------------------------

    else:

        if not user_can_comment_review(
            request.user,
            review,
        ):
            if is_htmx(request):
                return _review_region(request, review.product)
            messages.error(request, _("Your previous comment is still awaiting approval."))
            return redirect(f"{review.product.get_absolute_url()}#reviews")
            return JsonResponse(
                {
                    "success": False,
                    "message": (
                        _("Your previous comment is still awaiting approval.")
                    ),
                },
                status=400,
            )

    # ----------------------------------------------------------
    # Validate form
    # ----------------------------------------------------------

    form = ReviewCommentForm(request.POST)

    if not form.is_valid():

        if is_htmx(request):
            return _review_region(request, review.product)

        messages.error(request, _("Please correct the errors in your reply."))
        return redirect(f"{review.product.get_absolute_url()}#reviews")

    # ----------------------------------------------------------
    # Save
    # ----------------------------------------------------------

    comment = form.save(commit=False)

    comment.review = review
    comment.user = request.user
    comment.parent = parent

    comment.save()

    # ----------------------------------------------------------
    # Response
    # ----------------------------------------------------------

    if is_htmx(request):
        return _review_region(request, review.product)
    messages.success(request, _("Your comment was submitted for moderation."))
    return redirect(f"{review.product.get_absolute_url()}#reviews")


@login_required
@require_POST
def edit_comment(request, pk):
    comment = get_object_or_404(
        ReviewComment,
        pk=pk,
        user=request.user,
    )

    form = ReviewCommentForm(
        request.POST,
        instance=comment,
    )

    if not form.is_valid():
        if is_htmx(request):
            return _review_region(request, comment.review.product)
        messages.error(request, _("Please correct the errors in your reply."))
        return redirect(f"{comment.review.product.get_absolute_url()}#reviews")

    comment = form.save(commit=False)
    comment.is_approved = False
    comment.save()

    if is_htmx(request):
        return _review_region(request, comment.review.product)
    messages.success(request, _("Your edited reply now requires moderation again."))
    return redirect(f"{comment.review.product.get_absolute_url()}#reviews")


@login_required
@require_POST
def delete_comment(request, pk):
    comment = get_object_or_404(
        ReviewComment,
        pk=pk,
        user=request.user,
    )

    product = comment.review.product
    comment.delete()

    if is_htmx(request):
        return _review_region(request, product)
    messages.success(request, _("Reply deleted."))
    return redirect(f"{product.get_absolute_url()}#reviews")
