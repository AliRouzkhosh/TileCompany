############ dashboard/selectors/moderation.py ################

from orders.models import Order, OrderItem
from products.models import Product, ProductView

from blog.models import (
    Comment,
    Article,
    ArticleStatus,
)

from reviews.models import (
    Review,
    ReviewComment,
)

# ==========================================================
# Blog Comments
# ==========================================================
def get_pending_blog_comments():
    """
    Blog comments awaiting moderation.
    """

    return (
        Comment.objects
        .filter(is_approved=False)
        .select_related(
            "article",
            "user",
        )
        .order_by("created_at")
    )

def get_blog_comment(comment_id):
    """
    Retrieve a single blog comment.
    """

    return (
        Comment.objects
        .select_related(
            "article",
            "user",
        )
        .get(pk=comment_id)
    )
# ==========================================================
# Reviews
# ==========================================================
def get_pending_reviews():
    """
    Product reviews awaiting moderation.
    """

    return (
        Review.objects
        .filter(is_approved=False)
        .select_related(
            "product",
            "user",
        )
        .order_by("created_at")
    )

def get_review(review_id):
    """
    Retrieve a single product review.
    """

    return (
        Review.objects
        .select_related(
            "product",
            "user",
        )
        .get(pk=review_id)
    )
# ==========================================================
# Review Comments
# ==========================================================
def get_pending_review_comments():
    """
    Review replies awaiting moderation.
    """

    return (
        ReviewComment.objects
        .filter(is_approved=False)
        .select_related(
            "review",
            "review__product",
            "user",
        )
        .order_by("created_at")
    )
def get_review_comment(comment_id):
    """
    Retrieve a single review comment.
    """

    return (
        ReviewComment.objects
        .select_related(
            "review",
            "review__product",
            "user",
        )
        .get(pk=comment_id)
    )
# ==========================================================
# Articles
# ==========================================================

def get_draft_articles():
    """
    Draft articles.
    """

    return (
        Article.objects
        .filter(
            status=ArticleStatus.DRAFT,
        )
        .select_related(
            "author",
            "category",
        )
        .order_by("-created_at")
    )


def get_published_articles():
    """
    Published articles.
    """

    return (
        Article.objects
        .filter(
            status=ArticleStatus.PUBLISHED,
        )
        .select_related(
            "author",
            "category",
        )
        .order_by("-published_at")
    )
