from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db.models import Q, Prefetch
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from dashboard.models import StaffAccessAuditEvent
from dashboard.permissions import owner_required, user_can_access_dashboard
from dashboard.role_definitions import (
    DASHBOARD_CAPABILITY_PERMISSIONS,
    ROLE_DESCRIPTIONS,
    role_display_label,
)
from dashboard.services_staff_access import MANAGED_ROLE_NAMES, managed_roles_for_user, set_managed_dashboard_roles


def _direct_dashboard_permissions(user):
    return user.user_permissions.filter(content_type__app_label__in={p.split('.', 1)[0] for p in DASHBOARD_CAPABILITY_PERMISSIONS}).exists()


@owner_required
def staff_list(request):
    query = request.GET.get("q", "").strip()
    User = get_user_model()
    users = User.objects.prefetch_related("groups", "user_permissions__content_type").order_by("username")
    if query:
        users = users.filter(Q(username__icontains=query) | Q(email__icontains=query))
    page_obj = Paginator(users, 25).get_page(request.GET.get("page"))
    rows = [{"user": user, "roles": [role_display_label(name) for name in managed_roles_for_user(user)], "effective_access": user_can_access_dashboard(user), "has_direct_permissions": _direct_dashboard_permissions(user)} for user in page_obj]
    return render(request, "dashboard/staff_access/list.html", {"rows": rows, "page_obj": page_obj, "query": query, "role_descriptions": ROLE_DESCRIPTIONS})


@owner_required
def staff_detail(request, user_id):
    target = get_object_or_404(get_user_model().objects.prefetch_related("groups", "user_permissions__content_type"), pk=user_id)
    return render(request, "dashboard/staff_access/detail.html", {"target": target, "managed_roles": managed_roles_for_user(target), "role_cards": [(name, role_display_label(name), ROLE_DESCRIPTIONS[name]) for name in MANAGED_ROLE_NAMES], "effective_access": user_can_access_dashboard(target), "has_direct_permissions": _direct_dashboard_permissions(target)})


@owner_required
def update_roles(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    target = get_object_or_404(get_user_model(), pk=user_id)
    try:
        changed, before_roles, after_roles = set_managed_dashboard_roles(actor=request.user, target_user=target, role_names=request.POST.getlist("roles"))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("dashboard:staff_access:detail", user_id=target.pk)
    messages.success(request, _("Access roles updated for %(username)s.") % {"username": target.username} if changed else _("No access-role changes were needed."))
    return redirect("dashboard:staff_access:detail", user_id=target.pk)


@owner_required
def staff_history(request):
    page_obj = Paginator(StaffAccessAuditEvent.objects.select_related("actor", "target_user"), 25).get_page(request.GET.get("page"))
    history_rows = [{"event": event, "before": [role_display_label(name) for name in event.roles_before], "after": [role_display_label(name) for name in event.roles_after]} for event in page_obj]
    return render(request, "dashboard/staff_access/history.html", {"page_obj": page_obj, "history_rows": history_rows})
