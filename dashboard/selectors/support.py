from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from support.models import Ticket, TicketCategory, TicketPriority, TicketReply, TicketStatus
from dashboard.request_cache import get_request_value

ACTIONABLE = (TicketStatus.OPEN, TicketStatus.WAITING_SUPPORT)
def support_queryset(): return Ticket.objects.select_related("user", "category", "order", "assigned_to").prefetch_related(Prefetch("replies", queryset=TicketReply.objects.select_related("author").prefetch_related("attachments")), "notes__author", "attachments", "activities__actor")
def response_state(ticket):
    if ticket.status not in ACTIONABLE: return None, None
    start = ticket.created_at if ticket.status == TicketStatus.OPEN else (ticket.last_customer_reply_at or ticket.last_reply_at or ticket.created_at)
    hours = (timezone.now() - start).total_seconds() / 3600
    return ("overdue" if hours > settings.SUPPORT_RESPONSE_TARGET_HOURS else "attention" if hours >= settings.SUPPORT_RESPONSE_WARNING_HOURS else "healthy"), start
def get_support_tickets(**filters):
    qs = support_queryset(); queue = filters.get("queue")
    if queue == "needs_response": qs=qs.filter(status__in=ACTIONABLE)
    elif queue == "mine": qs=qs.filter(assigned_to=filters.get("user"))
    elif queue == "unassigned": qs=qs.filter(assigned_to__isnull=True)
    elif queue == "urgent": qs=qs.filter(priority=TicketPriority.URGENT, status__in=ACTIONABLE)
    elif queue == "waiting_customer": qs=qs.filter(status=TicketStatus.WAITING_CUSTOMER)
    elif queue == "resolved": qs=qs.filter(status=TicketStatus.RESOLVED)
    for key in ("status", "priority", "category"):
        if filters.get(key): qs=qs.filter(**{key: filters[key]})
    if filters.get("assignee"): qs=qs.filter(assigned_to_id=filters["assignee"])
    if filters.get("search"):
        term=filters["search"]; qs=qs.filter(Q(pk__icontains=term)|Q(subject__icontains=term)|Q(message__icontains=term)|Q(user__username__icontains=term)|Q(user__first_name__icontains=term)|Q(user__last_name__icontains=term)|Q(user__email__icontains=term)|Q(order__user_order_number__icontains=term)|Q(category__name__icontains=term)|Q(category__name_fa__icontains=term))
    if filters.get("sla") == "overdue": qs=qs.filter(status__in=ACTIONABLE, created_at__lt=timezone.now()-timedelta(hours=settings.SUPPORT_RESPONSE_TARGET_HOURS))
    return qs.order_by("-priority", "created_at")
def get_ticket_for_dashboard(pk): return get_object_or_404(support_queryset(), pk=pk)
def get_support_queue_counts(user=None, *, request=None):
    base=Ticket.objects
    actor_id = getattr(user, "pk", None)
    return {
        "needs_response": get_request_value(request, ("support", "needs_response", actor_id), lambda: base.filter(status__in=ACTIONABLE).count()),
        "mine": get_request_value(request, ("support", "mine", actor_id), lambda: base.filter(assigned_to=user, status__in=ACTIONABLE).count()) if user else 0,
        "unassigned": get_request_value(request, ("support", "unassigned", actor_id), lambda: base.filter(assigned_to__isnull=True,status__in=ACTIONABLE).count()),
        "urgent": get_request_value(request, ("support", "urgent", actor_id), lambda: base.filter(priority=TicketPriority.URGENT,status__in=ACTIONABLE).count()),
    }
def get_ticket_categories_with_counts(): return TicketCategory.objects.annotate(ticket_count=Count("tickets"))
def support_agents(): return get_user_model().objects.with_perm("support.change_ticket", is_active=True, include_superusers=True).order_by("username")
