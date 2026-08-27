from .models import Notification


def get_user_notifications(user, category=None):
    notifications = Notification.objects.filter(user=user)
    if category:
        notifications = notifications.filter(category=category)
    return notifications


def get_unread_notifications(user):
    return get_user_notifications(user).filter(is_read=False)


def get_unread_notification_count(user):
    return get_unread_notifications(user).count()


def get_recent_notifications(user, limit=5):
    return get_user_notifications(user)[:limit]


def get_recent_unread_notifications(user, limit=5):
    return get_unread_notifications(user)[:limit]
