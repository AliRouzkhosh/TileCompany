from django.contrib import admin

from dashboard.models import StaffAccessAuditEvent


@admin.register(StaffAccessAuditEvent)
class StaffAccessAuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor_username", "target_username")
    readonly_fields = ("actor", "target_user", "actor_username", "actor_email", "target_username", "target_email", "action", "roles_before", "roles_after", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
