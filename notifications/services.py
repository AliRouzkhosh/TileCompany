import json
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from .events import validate_event_category
from .models import Notification


def _normalize_payload(payload):
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ValidationError({"payload": "Notification payload must be an object."})

    normalized = dict(payload)
    try:
        json.dumps(normalized, cls=DjangoJSONEncoder)
    except (TypeError, ValueError) as error:
        raise ValidationError({"payload": "Notification payload must be JSON serializable."}) from error
    return normalized


def _normalize_target_url(target_url):
    if not target_url:
        return ""

    parsed = urlsplit(target_url)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        raise ValidationError({"target_url": "Notification targets must be internal paths."})
    return urlunsplit(("", "", parsed.path, parsed.query, ""))


def get_safe_internal_target(target_url):
    try:
        return _normalize_target_url(target_url)
    except ValidationError:
        return ""


def reverse_notification_target(viewname, *, kwargs=None):
    """Reverse a stable internal target independent of the active request language."""
    with override(settings.LANGUAGE_CODE):
        return _normalize_target_url(reverse(viewname, kwargs=kwargs))


@transaction.atomic
def create_notification(*, user, category, event_type, payload=None, target_url=""):
    validate_event_category(category=category, event_type=event_type)
    notification = Notification(
        user=user,
        category=category,
        event_type=event_type,
        payload=_normalize_payload(payload),
        target_url=_normalize_target_url(target_url),
    )
    notification.full_clean()
    notification.save()
    return notification


@transaction.atomic
def create_notification_once(*, user, category, event_type, payload=None, target_url=""):
    """Create one notification for an immutable workflow event payload."""
    validate_event_category(category=category, event_type=event_type)
    normalized_payload = _normalize_payload(payload)
    normalized_target = _normalize_target_url(target_url)
    existing = Notification.objects.filter(
        user=user,
        category=category,
        event_type=event_type,
        payload=normalized_payload,
    ).first()
    if existing is not None:
        return existing, False
    notification = Notification(
        user=user,
        category=category,
        event_type=event_type,
        payload=normalized_payload,
        target_url=normalized_target,
    )
    notification.full_clean()
    notification.save()
    return notification, True


@transaction.atomic
def mark_notification_read(*, notification, user):
    locked_notification = Notification.objects.select_for_update().get(pk=notification.pk)
    if locked_notification.user_id != user.id:
        raise PermissionDenied("You cannot modify another user's notification.")
    if not locked_notification.is_read:
        locked_notification.is_read = True
        locked_notification.read_at = timezone.now()
        locked_notification.save(update_fields=("is_read", "read_at"))

    notification.is_read = locked_notification.is_read
    notification.read_at = locked_notification.read_at
    return locked_notification


@transaction.atomic
def mark_all_notifications_read(*, user):
    return Notification.objects.filter(user=user, is_read=False).update(
        is_read=True,
        read_at=timezone.now(),
    )
