from django.conf import settings
from django.contrib.auth.models import Permission
from django.db import models


class DashboardAuthorization(Permission):
    """Permission anchor for Dashboard-only capabilities."""

    class Meta:
        proxy = True
        verbose_name = "Dashboard authorization"
        verbose_name_plural = "Dashboard authorizations"
        permissions = [
            ("view_sensitive_analytics", "Can view sensitive dashboard analytics"),
        ]


class StaffAccessAuditEvent(models.Model):
    class Action(models.TextChoices):
        ROLES_CHANGED = "roles_changed", "Roles changed"
        DASHBOARD_ACCESS_REVOKED = "dashboard_access_revoked", "Dashboard access revoked"

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="staff_access_actions")
    target_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="staff_access_events")
    actor_username = models.CharField(max_length=150)
    actor_email = models.EmailField(blank=True)
    target_username = models.CharField(max_length=150)
    target_email = models.EmailField(blank=True)
    action = models.CharField(max_length=32, choices=Action.choices)
    roles_before = models.JSONField(default=list)
    roles_after = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
