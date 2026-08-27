from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "category", "event_type", "is_read", "created_at")
    list_filter = ("category", "is_read", "created_at")
    search_fields = ("user__username", "user__email", "event_type")
    readonly_fields = ("created_at", "read_at")
    ordering = ("-created_at",)

