from .presentation import get_notification_displays
from .selectors import get_recent_unread_notifications, get_unread_notification_count


def notification_header(request):
    if not request.user.is_authenticated:
        return {"notification_unread_count": 0, "notification_previews": []}

    unread_notifications = get_recent_unread_notifications(request.user, limit=5)
    return {
        "notification_unread_count": get_unread_notification_count(request.user),
        "notification_previews": get_notification_displays(unread_notifications),
    }
