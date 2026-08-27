#################### dashboard/views/home.py #################
from django.shortcuts import render

from dashboard.notifications import get_inventory_alerts
from dashboard.permissions import dashboard_access_required

from dashboard.selectors import (
    get_fulfillment_queue,
    get_in_transit_queue,
    get_refund_queue,
    get_refund_processing_queue,
    get_attention_queue,
)
from blog.models import Article, ArticleStatus
from support.models import Ticket, TicketStatus
from dashboard.request_cache import get_request_value


@dashboard_access_required
def dashboard_home(request):
    user = request.user
    can_products = user.has_perm("products.view_product")
    can_inventory = user.has_perm("products.change_inventory")
    can_orders = user.has_perm("orders.view_order")
    can_support = user.has_perm("support.view_ticket")
    can_content = user.has_perm("blog.view_article")
    can_blog_moderation = user.has_perm("blog.moderate_article_comments")
    can_review_moderation = user.has_perm("reviews.moderate_reviews")
    can_analytics = user.has_perm("dashboard.view_sensitive_analytics")
    context = {"can_products": can_products, "can_inventory": can_inventory, "can_orders": can_orders, "can_support": can_support, "can_content": can_content, "can_blog_moderation": can_blog_moderation, "can_review_moderation": can_review_moderation, "can_analytics": can_analytics, "is_owner": user.is_superuser}
    if can_inventory:
        inventory_alerts = get_inventory_alerts(user, request=request)
        context.update(inventory_alerts)
        context["inventory_alert_preview"] = inventory_alerts["items"][:2]
        context["inventory_alert_more_count"] = max(
            inventory_alerts["low_stock_count"] + inventory_alerts["out_of_stock_count"]
            - len(context["inventory_alert_preview"]),
            0,
        )
    if can_orders:
        context.update(fulfillment_orders=get_fulfillment_queue()[:5], in_transit_orders=get_in_transit_queue()[:5], attention_orders=get_attention_queue()[:5])
    if user.is_superuser:
        context.update(refund_orders=get_refund_queue()[:5], refund_processing_orders=get_refund_processing_queue()[:5])
    if can_support:
        context["support_open_count"] = get_request_value(
            request,
            ("support", "needs_response", user.pk),
            lambda: Ticket.objects.filter(status__in=(TicketStatus.OPEN, TicketStatus.WAITING_SUPPORT)).count(),
        )
    if can_content:
        context.update(article_count=Article.objects.count(), draft_article_count=Article.objects.filter(status=ArticleStatus.DRAFT).count())

    return render(
        request,
        "dashboard/home/index.html",
        context,
    )
