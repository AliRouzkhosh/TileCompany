from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse, translate_url
from django.utils.translation import get_language_from_path, gettext as _, pgettext
from django.views.decorators.http import require_POST
from TileCompany.htmx import is_htmx_target

from .models import Notification
from .presentation import get_notification_displays
from .selectors import get_user_notifications
from .services import get_safe_internal_target, mark_all_notifications_read, mark_notification_read


def _selected_category(value):
    return value if value in Notification.Category.values else None


def _center_url(category=None):
    url = reverse("notifications:center")
    return f"{url}?category={category}" if category else url


def _target_in_request_language(target, request):
    language = get_language_from_path(request.path) or request.LANGUAGE_CODE
    translated = translate_url(target, language)
    if get_language_from_path(translated) == language:
        return translated

    parsed = urlsplit(target)
    source_language = get_language_from_path(parsed.path)
    supported_languages = {code for code, _label in settings.LANGUAGES}
    if source_language and language in supported_languages:
        suffix = parsed.path.removeprefix(f"/{source_language}")
        return urlunsplit(("", "", f"/{language}{suffix}", parsed.query, ""))
    return translated


@login_required
def notification_center(request):
    category = _selected_category(request.GET.get("category"))
    page_obj = Paginator(get_user_notifications(request.user, category=category), 20).get_page(request.GET.get("page"))
    context = {
            "notification_items": get_notification_displays(page_obj.object_list, resolve_current_actions=True),
            "page_obj": page_obj,
            "selected_category": category,
            "notification_categories": (
                ("order", _("Orders")), ("refund", _("Refunds")),
                ("review", _("Reviews")), ("content", _("Content")),
                ("support", _("Support")),
            ),
            "breadcrumbs": [
                {"label": pgettext("account breadcrumb", "My Account"), "url": reverse("accounts:overview")},
                {"label": _("Notifications"), "url": None},
            ],
        }
    if is_htmx_target(request, "account-workspace"):
        context["account_content_template"] = "notifications/components/notification_workspace.html"
        return render(request, "accounts/components/account_workspace.html", context)
    if is_htmx_target(request, "notification-workspace"):
        return render(request, "notifications/components/notification_workspace.html", context)
    return render(request, "notifications/center.html", context)


@login_required
@require_POST
def open_notification(request, notification_id):
    try:
        notification = Notification.objects.get(pk=notification_id, user=request.user)
    except Notification.DoesNotExist as error:
        raise Http404 from error

    mark_notification_read(notification=notification, user=request.user)
    target = get_safe_internal_target(notification.target_url)
    if target:
        return redirect(_target_in_request_language(target, request))
    return redirect(_center_url(_selected_category(request.POST.get("category"))))


@login_required
@require_POST
def mark_all_read(request):
    category = _selected_category(request.POST.get("category"))
    mark_all_notifications_read(user=request.user)
    return redirect(_center_url(category))
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
