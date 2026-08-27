############## support/admin.py ##############3
# support/admin.py

from django.contrib import admin

from .models import Ticket, TicketReply, TicketCategory, TicketNote, TicketActivity, TicketAttachment, HelpArticle


class TicketReplyInline(admin.TabularInline):
    model = TicketReply
    extra = 0

    fields = (
        "author",
        "message",
        "is_staff_reply",
        "created_at",
    )

    readonly_fields = (
        "created_at",
    )

    autocomplete_fields = (
        "author",
    )

    ordering = ("created_at",)

@admin.register(TicketCategory)
class TicketCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "name_fa",
        "slug",
        "icon",
    )

    search_fields = (
        "name",
        "name_fa",
        "description",
        "description_fa",
    )

    prepopulated_fields = {
        "slug": ("name",),
    }

    ordering = (
        "name",
    )
@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "subject",
        "category",
        "priority",
        "user",
        "status",
        "order",
        "reply_count",
        "last_reply_at",
        "created_at",
    )

    list_filter = (
        "category",
        "status",
        "created_at",
        "priority",
        "last_reply_at",
    )

    search_fields = (
        "subject",
        "message",
        "user__username",
        "user__email",
        "order__user_order_number",
    )

    ordering = (
        "-last_reply_at",
    )

    autocomplete_fields = (
        "user",
        "order",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "last_reply_at",
    )

    list_editable = (
        "status",
        "priority",
    )

    date_hierarchy = "created_at"

    inlines = (
        TicketReplyInline,
    )

    fieldsets = (
        (
            "Ticket",
            {
                "fields": (
                    "user",
                    "order",
                    "status",
                    "subject",
                    "message",
                )
            },
        ),
        (
            "Activity",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                    "last_reply_at",
                )
            },
        ),
    )

    @admin.display(description="Replies")
    def reply_count(self, obj):
        return obj.replies.count()

    def save_formset(self, request, form, formset, change):
        """
        Automatically mark replies created in the admin
        as staff replies.
        """
        instances = formset.save(commit=False)

        for instance in instances:
            if isinstance(instance, TicketReply):
                if not instance.pk:
                    instance.author = request.user
                    instance.is_staff_reply = True

                instance.save()

        formset.save_m2m()

admin.site.register(TicketNote)
admin.site.register(TicketActivity)
admin.site.register(TicketAttachment)
admin.site.register(HelpArticle)
