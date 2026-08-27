from django.core.exceptions import ValidationError

from .models import Notification, NotificationEvent


EVENT_CATEGORIES = {
    NotificationEvent.PAYMENT_FAILED: Notification.Category.ORDER,
    NotificationEvent.PAYMENT_CANCELLED: Notification.Category.ORDER,
    NotificationEvent.ORDER_CONFIRMED: Notification.Category.ORDER,
    NotificationEvent.ORDER_ON_HOLD: Notification.Category.ORDER,
    NotificationEvent.ORDER_RESUMED: Notification.Category.ORDER,
    NotificationEvent.ORDER_SHIPPED: Notification.Category.ORDER,
    NotificationEvent.ORDER_DELIVERED: Notification.Category.ORDER,
    NotificationEvent.ORDER_CANCELLED: Notification.Category.ORDER,
    NotificationEvent.REFUND_APPROVED: Notification.Category.REFUND,
    NotificationEvent.REFUND_NEEDS_INFORMATION: Notification.Category.REFUND,
    NotificationEvent.REFUND_REJECTED: Notification.Category.REFUND,
    NotificationEvent.REFUND_PROCESSED: Notification.Category.REFUND,
    NotificationEvent.REVIEW_APPROVED: Notification.Category.REVIEW,
    NotificationEvent.REVIEW_COMMENT_APPROVED: Notification.Category.REVIEW,
    NotificationEvent.ARTICLE_COMMENT_APPROVED: Notification.Category.CONTENT,
    NotificationEvent.SUPPORT_REPLY: Notification.Category.SUPPORT,
    NotificationEvent.SUPPORT_STATUS_CHANGED: Notification.Category.SUPPORT,
}


def validate_event_category(*, category, event_type):
    expected_category = EVENT_CATEGORIES.get(event_type)
    if expected_category is None:
        raise ValidationError({"event_type": "Unsupported notification event type."})
    if category != expected_category:
        raise ValidationError({"category": "This event type does not belong to the selected category."})
