from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


class NotificationEvent(models.TextChoices):
    PAYMENT_FAILED = "payment_failed", _("Payment failed")
    PAYMENT_CANCELLED = "payment_cancelled", _("Payment cancelled")
    ORDER_CONFIRMED = "order_confirmed", _("Order confirmed")
    ORDER_ON_HOLD = "order_on_hold", _("Order on hold")
    ORDER_RESUMED = "order_resumed", _("Order resumed")
    ORDER_SHIPPED = "order_shipped", _("Order shipped")
    ORDER_DELIVERED = "order_delivered", _("Order delivered")
    ORDER_CANCELLED = "order_cancelled", _("Order cancelled")
    REFUND_APPROVED = "refund_approved", _("Refund approved")
    REFUND_NEEDS_INFORMATION = "refund_needs_information", _("More information required for refund")
    REFUND_REJECTED = "refund_rejected", _("Refund rejected")
    REFUND_PROCESSED = "refund_processed", _("Refund processed")
    REVIEW_APPROVED = "review_approved", _("Review approved")
    REVIEW_COMMENT_APPROVED = "review_comment_approved", _("Review comment approved")
    ARTICLE_COMMENT_APPROVED = "article_comment_approved", _("Article comment approved")
    SUPPORT_REPLY = "support_reply", _("New support reply")
    SUPPORT_STATUS_CHANGED = "support_status_changed", _("Support status changed")


class Notification(models.Model):
    class Category(models.TextChoices):
        ORDER = "order", _("Order")
        REFUND = "refund", _("Refund")
        REVIEW = "review", _("Review")
        CONTENT = "content", _("Content")
        SUPPORT = "support", _("Support")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    category = models.CharField(max_length=20, choices=Category.choices)
    event_type = models.CharField(max_length=50, choices=NotificationEvent.choices)
    payload = models.JSONField(default=dict, blank=True)
    target_url = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(fields=("user", "-created_at"), name="notification_user_recent_idx"),
            models.Index(fields=("user", "is_read"), name="notification_user_unread_idx"),
        ]

    def __str__(self):
        return f"{self.user} · {self.event_type}"

    def clean(self):
        from .events import validate_event_category

        validate_event_category(category=self.category, event_type=self.event_type)
        if self.target_url:
            parsed = urlsplit(self.target_url)
            if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
                raise ValidationError({"target_url": "Notification targets must be internal paths."})
