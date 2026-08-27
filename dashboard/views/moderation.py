# dashboard/views/moderation.py

from django.contrib import messages
from django.shortcuts import (
    render,
    redirect,
)
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _

from dashboard.permissions import dashboard_permission_required

from dashboard.selectors import (
    get_pending_blog_comments,
    get_pending_reviews,
    get_pending_review_comments,
    get_blog_comment,
    get_review,
    get_review_comment,
)

from dashboard.actions import (
    execute_moderation_action,
)


# ==========================================================
# Moderation Home
# ==========================================================

@dashboard_permission_required(
    "reviews.moderate_reviews", "blog.moderate_article_comments", any_of=True,
)
def moderation_home(request):
    """
    Moderation dashboard overview.
    """

    can_moderate_blog = request.user.has_perm("blog.moderate_article_comments")
    can_moderate_reviews = request.user.has_perm("reviews.moderate_reviews")
    blog_comments = get_pending_blog_comments() if can_moderate_blog else ()
    reviews = get_pending_reviews() if can_moderate_reviews else ()
    review_comments = get_pending_review_comments() if can_moderate_reviews else ()

    context = {
        "blog_comments": blog_comments,
        "reviews": reviews,
        "review_comments": review_comments,
        "can_moderate_blog": can_moderate_blog,
        "can_moderate_reviews": can_moderate_reviews,
        "pending_count": (
            (blog_comments.count() if can_moderate_blog else 0)
            + (reviews.count() if can_moderate_reviews else 0)
            + (review_comments.count() if can_moderate_reviews else 0)
        ),
    }

    return render(
        request,
        "dashboard/moderation/index.html",
        context,
    )


# ==========================================================
# Blog Comments Queue
# ==========================================================

@dashboard_permission_required("blog.view_comment", "blog.moderate_article_comments")
def blog_comment_queue(request):
    """
    Pending blog comments.
    """

    comments = get_pending_blog_comments()

    return render(
        request,
        "dashboard/moderation/blog_comments.html",
        {
            "comments": comments,
            "pending_count": comments.count(),
        },
    )


# ==========================================================
# Product Reviews Queue
# ==========================================================

@dashboard_permission_required("reviews.view_review", "reviews.moderate_reviews")
def review_queue(request):
    """
    Pending product reviews.
    """

    reviews = get_pending_reviews()

    return render(
        request,
        "dashboard/moderation/reviews.html",
        {
            "reviews": reviews,
            "pending_count": reviews.count(),
        },
    )


# ==========================================================
# Review Replies Queue
# ==========================================================

@dashboard_permission_required("reviews.view_reviewcomment", "reviews.moderate_reviews")
def review_comment_queue(request):
    """
    Pending review replies.
    """

    review_comments = get_pending_review_comments()

    return render(
        request,
        "dashboard/moderation/review_comments.html",
        {
            "review_comments": review_comments,
            "pending_count": review_comments.count(),
        },
    )


# ==========================================================
# Blog Comments Actions
# ==========================================================

@dashboard_permission_required("blog.moderate_article_comments")
@require_POST
def approve_blog_comment(request, comment_id):

    comment = get_blog_comment(comment_id)

    if execute_moderation_action(
        "approve_blog_comment",
        comment,
        request.user,
    ):
        messages.success(
            request,
            _("Blog comment approved."),
        )
    else:
        messages.error(
            request,
            _("Unable to approve blog comment."),
        )

    return redirect("dashboard:blog_comment_queue")


@dashboard_permission_required("blog.moderate_article_comments")
@require_POST
def reject_blog_comment(request, comment_id):

    comment = get_blog_comment(comment_id)

    if execute_moderation_action(
        "reject_blog_comment",
        comment,
        request.user,
    ):
        messages.success(
            request,
            _("Blog comment rejected."),
        )
    else:
        messages.error(
            request,
            _("Unable to reject blog comment."),
        )

    return redirect("dashboard:blog_comment_queue")


# ==========================================================
# Review Actions
# ==========================================================

@dashboard_permission_required("reviews.moderate_reviews")
@require_POST
def approve_review(request, review_id):

    review = get_review(review_id)

    if execute_moderation_action(
        "approve_review",
        review,
        request.user,
    ):
        messages.success(
            request,
            _("Review approved."),
        )
    else:
        messages.error(
            request,
            _("Unable to approve review."),
        )

    return redirect("dashboard:review_queue")


@dashboard_permission_required("reviews.moderate_reviews")
@require_POST
def reject_review(request, review_id):

    review = get_review(review_id)

    if execute_moderation_action(
        "reject_review",
        review,
        request.user,
    ):
        messages.success(
            request,
            _("Review rejected."),
        )
    else:
        messages.error(
            request,
            _("Unable to reject review."),
        )

    return redirect("dashboard:review_queue")


# ==========================================================
# Review Comment Actions
# ==========================================================

@dashboard_permission_required("reviews.moderate_reviews")
@require_POST
def approve_review_comment(request, comment_id):

    comment = get_review_comment(comment_id)

    if execute_moderation_action(
        "approve_review_comment",
        comment,
        request.user,
    ):
        messages.success(
            request,
            _("Review comment approved."),
        )
    else:
        messages.error(
            request,
            _("Unable to approve review comment."),
        )

    return redirect("dashboard:review_comment_queue")


@dashboard_permission_required("reviews.moderate_reviews")
@require_POST
def reject_review_comment(request, comment_id):

    comment = get_review_comment(comment_id)

    if execute_moderation_action(
        "reject_review_comment",
        comment,
        request.user,
    ):
        messages.success(
            request,
            _("Review comment rejected."),
        )
    else:
        messages.error(
            request,
            _("Unable to reject review comment."),
        )

    return redirect("dashboard:review_comment_queue")
