############# dashboard/context_processors.py ############
from dashboard.notifications import get_dashboard_notifications
from dashboard.selectors import (
    get_attention_queue,
    get_fulfillment_queue,
    get_in_transit_queue,
    get_refund_queue,
    get_refund_processing_queue,
    get_pending_blog_comments,
    get_pending_reviews,
    get_pending_review_comments,
)
from dashboard.selectors.support import get_support_queue_counts
from dashboard.permissions import user_can_access_dashboard
from dashboard.request_cache import get_request_value


def dashboard_access(request):
    """Expose only the Dashboard-entry capability to shared navigation."""
    return {
        "can_access_dashboard": user_can_access_dashboard(request.user),
    }


def dashboard_sidebar(request):
    """
    Global dashboard sidebar counters and notifications.
    """

    # Dashboard routes are wrapped in ``i18n_patterns``. Their actual paths
    # therefore begin with ``/en/dashboard/`` or ``/fa/dashboard/`` rather
    # than the old unprefixed ``/dashboard/`` path. Use the resolved
    # namespace so this context remains available in every locale.
    namespace = getattr(getattr(request, "resolver_match", None), "namespace", "")
    if not namespace.startswith("dashboard"):
        return {}

    user = request.user
    actor_id = user.pk
    can_view_orders = user.has_perm("orders.view_order")
    is_owner = user.is_superuser
    counts = {
        "fulfillment": 0,
        "in_transit": 0,
        "refunds": 0,
        "refund_processing": 0,
        "attention": 0,
        "moderation": 0,
        "support_needs_response": 0,
        "support_assigned_to_me": 0,
    }
    if can_view_orders:
        counts.update({
            "fulfillment": get_request_value(request, ("orders", "fulfillment", actor_id), lambda: get_fulfillment_queue().count()),
            "in_transit": get_request_value(request, ("orders", "in_transit", actor_id), lambda: get_in_transit_queue().count()),
            "attention": get_request_value(request, ("orders", "attention", actor_id), lambda: get_attention_queue().count()),
        })
    if is_owner:
        counts.update({
            "refunds": get_request_value(request, ("orders", "refund_review", actor_id), lambda: get_refund_queue().count()),
            "refund_processing": get_request_value(request, ("orders", "refund_processing", actor_id), lambda: get_refund_processing_queue().count()),
        })
    if user.has_perm("support.view_ticket"):
        support_counts = get_support_queue_counts(user, request=request)
        counts.update({
            "support_needs_response": support_counts["needs_response"],
            "support_assigned_to_me": support_counts["mine"],
        })
    can_moderate_blog = user.has_perm("blog.moderate_article_comments")
    can_moderate_reviews = user.has_perm("reviews.moderate_reviews")
    if can_moderate_blog or can_moderate_reviews:
        counts["moderation"] = (
            (get_request_value(request, ("moderation", "blog", actor_id), lambda: get_pending_blog_comments().count()) if can_moderate_blog else 0)
            + (get_request_value(request, ("moderation", "reviews", actor_id), lambda: get_pending_reviews().count()) if can_moderate_reviews else 0)
            + (get_request_value(request, ("moderation", "review_comments", actor_id), lambda: get_pending_review_comments().count()) if can_moderate_reviews else 0)
        )

    return {
        "dashboard_notifications": get_dashboard_notifications(user, request=request),
        "dashboard_counts": counts,
    }
