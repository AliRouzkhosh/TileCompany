# dashboard/urls/moderation.py

from django.urls import path

from dashboard.views.moderation import (
    moderation_home,
    blog_comment_queue,
    review_queue,
    review_comment_queue,
    approve_blog_comment,
    reject_blog_comment,
    approve_review,
    reject_review,
    approve_review_comment,
    reject_review_comment,
)

urlpatterns = [

    # ==========================================================
    # Moderation Home
    # ==========================================================

    path(
        "moderation/",
        moderation_home,
        name="moderation",
    ),

    # ==========================================================
    # Blog Comments
    # ==========================================================

    path(
        "moderation/blog-comments/",
        blog_comment_queue,
        name="blog_comment_queue",
    ),

    path(
        "moderation/blog-comments/<int:comment_id>/approve/",
        approve_blog_comment,
        name="approve_blog_comment",
    ),

    path(
        "moderation/blog-comments/<int:comment_id>/reject/",
        reject_blog_comment,
        name="reject_blog_comment",
    ),

    # ==========================================================
    # Product Reviews
    # ==========================================================

    path(
        "moderation/reviews/",
        review_queue,
        name="review_queue",
    ),

    path(
        "moderation/reviews/<int:review_id>/approve/",
        approve_review,
        name="approve_review",
    ),

    path(
        "moderation/reviews/<int:review_id>/reject/",
        reject_review,
        name="reject_review",
    ),

    # ==========================================================
    # Review Comments
    # ==========================================================

    path(
        "moderation/review-comments/",
        review_comment_queue,
        name="review_comment_queue",
    ),

    path(
        "moderation/review-comments/<int:comment_id>/approve/",
        approve_review_comment,
        name="approve_review_comment",
    ),

    path(
        "moderation/review-comments/<int:comment_id>/reject/",
        reject_review_comment,
        name="reject_review_comment",
    ),
]