# support/selectors.py

from django.shortcuts import get_object_or_404
from django.db.models import Count

from .models import Ticket, TicketReply, TicketCategory, HelpArticle


CUSTOMER_TICKET_STATUSES = ("all", "open", "waiting_customer", "waiting_support", "resolved", "closed")

def get_user_tickets(user, *, status="all", sort="latest"):
    """
    Return all tickets belonging to the given user.
    """
    if status not in CUSTOMER_TICKET_STATUSES:
        status = "all"
    if sort not in ("latest", "oldest"):
        sort = "latest"
    queryset = (
        Ticket.objects
        .filter(user=user)
        .select_related("category", "order", "refund_order")
    )
    if status != "all":
        queryset = queryset.filter(status=status)
    return queryset.order_by("updated_at" if sort == "oldest" else "-updated_at", "pk" if sort == "oldest" else "-pk")

def get_user_ticket_status_counts(user):
    counts = {status: 0 for status in CUSTOMER_TICKET_STATUSES}
    for row in Ticket.objects.filter(user=user).values("status").annotate(total=Count("pk")):
        counts[row["status"]] = row["total"]
    counts["all"] = sum(counts.values())
    return counts


def get_user_ticket(user, ticket_id):
    """
    Return a single ticket owned by the given user.
    Raises 404 if it doesn't exist.
    """
    return get_object_or_404(
        Ticket.objects.select_related("user", "category", "order", "refund_order").prefetch_related("attachments"),
        pk=ticket_id,
        user=user,
    )


def get_ticket_replies(ticket):
    """
    Return all replies for a ticket.
    """
    return (
        ticket.replies
        .select_related("author").prefetch_related("attachments")
        .all()
    )

def get_ticket_categories():
    """
    Return all ticket categories.
    """
    return TicketCategory.objects.order_by("name")

def get_user_orders(user):
    """
    Return the user's orders for the ticket creation form.
    """
    return user.orders.all()

def get_published_help_articles(search="", category=""):
    from django.db.models import Q
    qs = HelpArticle.objects.filter(is_published=True).select_related("category")
    if category: qs = qs.filter(category__slug=category)
    if search: qs = qs.filter(Q(title__icontains=search)|Q(title_fa__icontains=search)|Q(summary__icontains=search)|Q(summary_fa__icontains=search)|Q(content__icontains=search)|Q(content_fa__icontains=search))
    return qs
