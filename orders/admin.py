############## orders/admin.py #################
from django.contrib import admin
from django.contrib import messages
from .models import (
    Order,
    OrderItem,
    OrderStatusHistory,
    OrderNote,
)
from dashboard.services import (
    approve_refund as approve_refund_service,
    reject_refund as reject_refund_service,
)
from dashboard.services import (
    process_refund as process_refund_service,
)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = (
        "product",
        "variant",
        "product_name",
        "unit_price",
        "quantity",
    )
    can_delete = False


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = (
        "status",
        "note",
        "changed_at",
    )

class OrderNoteInline(admin.StackedInline):
    model = OrderNote
    extra = 0
    can_delete = False

    readonly_fields = (
        "author",
        "visibility",
        "category",
        "status",
        "operation_code",
        "created_at",
    )

    fields = (
        "author",
        "visibility",
        "category",
        "status",
        "operation_code",
        "message",
        "created_at",
    )
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user_order_number",
        "user",
        "status",
        "is_paid",
        "refund_status",
        "payment_provider",
        "total_amount",
        "created_at",
    )
    list_filter = (
        "status",
        "is_paid",
        "refund_status",
        "payment_provider",
        "created_at",
    )
    search_fields = (
        "id",
        "user__username",
        "full_name",
        "phone",
        "payment_reference",
        "notes__message",
    )
    list_editable = ("status",)
    readonly_fields = (
        "user",
        "user_order_number",
        "total_amount",
        "payment_provider",
        "payment_reference",
        "paid_at",
        "is_paid",
        "refund_requested_at",
        "refund_reviewed_at",
        "refund_processed_at",
        "refund_reviewed_by",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Customer Information",
            {
                "fields": (
                    "user",
                    "user_order_number",
                    "full_name",
                    "phone",
                    "address",
                    "city",
                )
            },
        ),
        (
            "Order",
            {
                "fields": (
                    "status",
                    "total_amount",
                )
            },
        ),
        (
            "Payment",
            {
                "fields": (
                    "is_paid",
                    "payment_provider",
                    "payment_reference",
                    "paid_at",
                )
            },
        ),
        (
            "Refund",
            {
                "fields": (
                    "refund_status",
                    "refund_reason",
                    "refund_message",
                    "refund_response",
                    "refund_requested_at",
                    "refund_reviewed_by",
                    "refund_reviewed_at",
                    "refund_processed_at",
                )
            },
        ),
        (
            "Timestamps",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
    inlines = [OrderItemInline, OrderStatusHistoryInline, OrderNoteInline,]
    actions = [
        "approve_refund",
        "reject_refund",
        "process_refund",
        "delete_cancelled_orders",
        "delete_selected",
    ]
    ordering = ("-created_at",)

    list_per_page = 50

    date_hierarchy = "created_at"

    def approve_refund(self, request, queryset):

        success = 0

        for order in queryset:

            if approve_refund_service(order, request.user):
                success += 1

        self.message_user(
            request,
            f"{success} refund(s) approved.",
            messages.SUCCESS,
        )

    def reject_refund(self, request, queryset):

        success = 0

        for order in queryset:

            if reject_refund_service(order, request.user):
                success += 1

        self.message_user(
            request,
            f"{success} refund(s) rejected.",
            messages.WARNING,
        )

    def process_refund(self, request, queryset):

        success = 0

        for order in queryset:

            if process_refund_service(order, request.user):
                success += 1

        self.message_user(
            request,
            f"{success} refund(s) processed.",
            messages.SUCCESS,
        )
    def save_model(self, request, obj, form, change):

        if change:
            old_status = Order.objects.get(pk=obj.pk).status

            super().save_model(request, obj, form, change)

            if old_status != obj.status:
                OrderStatusHistory.objects.create(
                    order=obj,
                    status=obj.status,
                    note=f"Status updated by staff ({request.user.username})."
                )

                OrderNote.objects.create(
                    order=obj,
                    author=request.user,
                    visibility=OrderNote.Visibility.INTERNAL,
                    category=OrderNote.Category.STATUS,
                    status=obj.status,
                    operation_code="STATUS_CHANGED",
                    message=(
                        f"Order status changed from "
                        f"{old_status} to {obj.get_status_display()} "
                        f"by {request.user.username}."
                    ),
                )

            return

        super().save_model(request, obj, form, change)


@admin.register(OrderNote)
class OrderNoteAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "category",
        "visibility",
        "author",
        "created_at",
    )

    list_filter = (
        "visibility",
        "category",
        "created_at",
    )

    search_fields = (
        "order__id",
        "message",
        "author__username",
    )

    readonly_fields = (
        "created_at",
    )

    ordering = ("-created_at",)
