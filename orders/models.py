############### orders/models.py ###############

from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from products.models import Product, ProductVariant
from products.currency import Currency
from accounts.models import Address
from django.utils.translation import get_language, gettext, gettext_lazy as _, pgettext


REFUND_WINDOW_HOURS = 72
REFUND_WINDOW = timedelta(hours=REFUND_WINDOW_HOURS)

class Order(models.Model):
    """
       Represents a customer's order throughout its lifecycle,
       from checkout through payment, fulfillment, delivery,
       cancellation, and future refund processing.
       """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        CONFIRMED = "confirmed", _("Confirmed")
        ON_HOLD = "on_hold", _("On Hold")
        SHIPPED = "shipped", _("Shipped")
        DELIVERED = "delivered", _("Delivered")
        CANCELLED = "cancelled", _("Cancelled")

    class ShippingMethod(models.TextChoices):
        STANDARD = "standard", _("Standard Delivery")
        EXPRESS = "express", _("Express Delivery")
        PICKUP = "pickup", _("Store Pickup")

    class PaymentMethod(models.TextChoices):
        ZARINPAL = "zarinpal", "ZarinPal"
        MOCK = "mock", _("Development Payment")
        ONLINE = "online", _("Online Payment")
        COD = "cod", _("Cash on Delivery")
        STRIP = "strip", _("Strip Delivery")
    # ---------------------------------------------------------
    # Customer
    # ---------------------------------------------------------

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="orders",
    )

    user_order_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Sequential order number for this customer.",
    )


    # ---------------------------------------------------------
    # Order Status
    # ---------------------------------------------------------

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    is_paid = models.BooleanField(
        default=False,
    )
    refund_requested_stage = models.CharField(
        max_length=20,
        choices=Status.choices,
        blank=True,
    )
    # ---------------------------------------------------------
    # Customer Information
    # ---------------------------------------------------------

    full_name = models.CharField(
        max_length=200,
    )

    address = models.TextField()

    city = models.CharField(
        max_length=100,
    )

    phone = models.CharField(
        max_length=20,
    )
    # ---------------------------------------------------------
    # Shipping
    # ---------------------------------------------------------

    shipping_address = models.ForeignKey(
        Address,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )

    shipping_method = models.CharField(
        max_length=50,
        choices=ShippingMethod.choices,
        default=ShippingMethod.STANDARD,
    )

    shipping_cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    delivery_slot = models.CharField(
        max_length=100,
        blank=True,
    )

    # ---------------------------------------------------------
    # Payment Method
    # ---------------------------------------------------------

    payment_method = models.CharField(
        max_length=50,
        choices=PaymentMethod.choices,
        default=PaymentMethod.ONLINE,
    )
    email_receipt_requested = models.BooleanField(default=False)
    receipt_email = models.EmailField(blank=True)
    communication_language = models.CharField(
        max_length=10,
        default="en",
        help_text="Immutable customer locale used for transactional order communication.",
    )
    # ---------------------------------------------------------
    # Financial
    # ---------------------------------------------------------

    total_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
    )

    checkout_attempt_token = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        editable=False,
        help_text="Durable idempotency token for the checkout submission that created this order.",
    )

    currency = models.CharField(
        max_length=3,
        choices=Currency.choices,
        default=Currency.USD,
        help_text="The single currency used by every monetary value in this order.",
    )

    payment_provider = models.CharField(
        max_length=50,
        blank=True,
    )

    payment_reference = models.CharField(
        max_length=200,
        blank=True,
    )

    paid_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    # ---------------------------------------------------------
    # Refund System (Post-payment cancellation workflow)
    # ---------------------------------------------------------
    class RefundStatus(models.TextChoices):
        NONE = "none", _("No Request")
        REQUESTED = "requested", _("Requested")
        NEEDS_INFORMATION = "needs_information", _("More information required")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        PROCESSED = "processed", _("Processed")

    class RefundReason(models.TextChoices):
        DAMAGED = "damaged", _("Product arrived damaged")
        WRONG_ITEM = "wrong_item", _("Wrong product received")
        QUALITY = "quality", _("Product quality issue")
        CHANGED_MIND = "changed_mind", _("Changed my mind")
        DELIVERY = "delivery", _("Delivery issue")
        OTHER = "other", _("Other")

    refund_reason = models.CharField(
        max_length=30,
        choices=RefundReason.choices,
        blank=True,
    )

    refund_status = models.CharField(
        max_length=20,
        choices=RefundStatus.choices,
        default=RefundStatus.NONE,
    )

    refund_support_ticket = models.OneToOneField(
        "support.Ticket",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refund_order",
        help_text="The customer support conversation for refund clarification.",
    )

    refund_message = models.TextField(
        blank=True,
    )

    refund_response = models.TextField(
        blank=True,
    )

    refund_reviewed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_refunds",
    )

    refund_reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    refund_requested_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    refund_processed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    inventory_restored_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When reserved variant inventory was returned to stock.",
    )

    # ---------------------------------------------------------
    # Timestamps
    # ---------------------------------------------------------

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    # ---------------------------------------------------------
    # Computed Properties
    # ---------------------------------------------------------

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING

    @property
    def is_confirmed(self):
        return self.status == self.Status.CONFIRMED

    @property
    def is_shipped(self):
        return self.status == self.Status.SHIPPED

    @property
    def is_on_hold(self):
        return self.status == self.Status.ON_HOLD

    @property
    def is_delivered(self):
        return self.status == self.Status.DELIVERED

    @property
    def is_cancelled(self):
        return self.status == self.Status.CANCELLED

    @property
    def is_cash_on_delivery(self):
        return self.payment_method == self.PaymentMethod.COD

    @property
    def requires_online_payment(self):
        """Whether this order uses one of the currently supported gateways."""
        return self.payment_method in {
            self.PaymentMethod.MOCK,
            self.PaymentMethod.ZARINPAL,
        }

    @property
    def can_start_online_payment(self):
        return (
            self.requires_online_payment
            and self.is_pending
            and not self.is_paid
        )

    @property
    def payment_permits_fulfillment(self):
        return self.is_paid or self.is_cash_on_delivery

    @property
    def payment_status_display(self):
        """Localized customer presentation derived from the existing payment semantics."""
        if self.is_paid:
            return _("Payment collected") if self.is_cash_on_delivery else _("Paid")
        if self.is_cash_on_delivery:
            return _("Payment due on delivery")
        if self.requires_online_payment:
            return _("Awaiting payment")
        return _("Payment status unavailable")

    @property
    def payment_status_kind(self):
        if self.is_paid:
            return "paid"
        if self.is_cash_on_delivery:
            return "due_on_delivery"
        if self.requires_online_payment:
            return "awaiting_payment"
        return "unavailable"

    @property
    def payment_provider_display(self):
        """Keep stored provider data intact while avoiding development-only UI text."""
        if self.payment_method == self.PaymentMethod.MOCK:
            return self.get_payment_method_display()
        if self.is_cash_on_delivery:
            return self.get_payment_method_display()
        return self.payment_provider

    @property
    def can_cancel(self):
        return (
            not self.is_paid
            and (
                self.is_pending and self.requires_online_payment
                or self.is_cash_on_delivery
                and self.status in {self.Status.CONFIRMED, self.Status.ON_HOLD}
            )
        )

    @property
    def refund_deadline(self):
        if not self.delivered_at:
            return None
        return self.delivered_at + REFUND_WINDOW

    @property
    def is_refund_window_closed(self):
        return (
            self.is_delivered
            and self.is_paid
            and self.refund_deadline is not None
            and timezone.now() > self.refund_deadline
        )

    @property
    def can_request_refund(self):

        if (
                not self.is_paid
                or self.status not in {
            self.Status.CONFIRMED,
            self.Status.ON_HOLD,
            self.Status.DELIVERED,
        }
        ):
            return False

        if self.is_delivered and (
            self.refund_deadline is None
            or timezone.now() > self.refund_deadline
        ):
            return False

        if self.refund_status == self.RefundStatus.NONE:
            return True

        if (
                self.refund_status == self.RefundStatus.REJECTED
                and self.refund_requested_stage != self.status
        ):
            return True

        return False
    @property
    def is_refund_pending(self):
        return (
                self.refund_status
                == self.RefundStatus.REQUESTED
        )

    @property
    def is_refund_approved(self):
        return (
                self.refund_status
                == self.RefundStatus.APPROVED
        )

    @property
    def is_refund_rejected(self):
        return (
                self.refund_status
                == self.RefundStatus.REJECTED
        )

    @property
    def is_refund_processed(self):
        return (
                self.refund_status
                == self.RefundStatus.PROCESSED
        )

    @property
    def is_refund_needs_information(self):
        return self.refund_status == self.RefundStatus.NEEDS_INFORMATION

    @property
    def is_refund_under_review(self):
        return self.refund_status in {
            self.RefundStatus.REQUESTED,
            self.RefundStatus.NEEDS_INFORMATION,
        }

    @property
    def has_active_refund(self):
        return self.refund_status in {
            self.RefundStatus.REQUESTED,
            self.RefundStatus.NEEDS_INFORMATION,
            self.RefundStatus.APPROVED,
        }

    @property
    def can_ship(self):
        return (
            self.is_confirmed
            and self.payment_permits_fulfillment
            and not self.has_active_refund
        )

    @property
    def can_hold(self):
        return self.can_ship

    @property
    def can_be_modified(self):
        """
        Returns True while the order has not entered the shipping process.
        """
        return self.status in {
            self.Status.PENDING,
            self.Status.CONFIRMED,
            self.Status.ON_HOLD,
        }

    @property
    def refund_status_badge(self):
        return self.get_refund_status_display()

    @property
    def subtotal(self):
        return sum(
            item.subtotal
            for item in self.items.all()
        )

    @property
    def discount_amount(self):
        return 0

    @property
    def tax_amount(self):
        return 0

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("manage_fulfillment", "Can manage order fulfillment"),
            ("process_refund", "Can process order refunds"),
        ]

    def __str__(self):
        order_number = self.user_order_number or self.pk

        return (
            f"Order #{order_number} "
            f"({self.user.username}) - {self.get_status_display()}"
        )


class OrderEmailDelivery(models.Model):
    """One durable, retryable transactional-email intent per order purpose."""

    class Kind(models.TextChoices):
        ONLINE_PAID_RECEIPT = "online_paid_receipt", _("Online paid receipt")
        COD_CONFIRMATION = "cod_confirmation", _("Cash on Delivery order confirmation")
        COD_PAID_RECEIPT = "cod_paid_receipt", _("Cash on Delivery paid receipt")

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SENDING = "sending", _("Sending")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="email_deliveries")
    kind = models.CharField(max_length=40, choices=Kind.choices)
    recipient = models.EmailField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order", "kind"], name="unique_order_email_delivery_kind"),
        ]
        ordering = ["created_at", "pk"]

    def __str__(self):
        return f"Order email {self.order_id} / {self.kind}"


class RefundAttachment(models.Model):
    """Customer-provided evidence attached to a refund request."""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="refund_attachments",
    )
    file = models.FileField(upload_to="orders/refunds/%Y/%m/")
    original_filename = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at", "pk"]

    def __str__(self):
        return self.original_filename or self.file.name


class OrderItem(models.Model):

    order = models.ForeignKey(
        Order,
        related_name="items",
        on_delete=models.CASCADE,
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    product_name = models.CharField(
        max_length=255,
    )

    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    quantity = models.PositiveIntegerField(
        default=1,
    )
    variant_snapshot = models.JSONField(
        default=dict,
        blank=True,
    )
    unit_reference_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Optional immutable list price in the Order currency at purchase time.",
    )

    @property
    def purchase_presentation(self):
        """Return a per-language cached, snapshot-authoritative history DTO."""

        from .purchase_presentation import build_purchased_line_presentation

        language = (get_language() or "en").lower().replace("_", "-")
        cache = self.__dict__.setdefault("_purchase_presentation_cache", {})
        if language not in cache:
            cache[language] = build_purchased_line_presentation(
                self, language=language
            )
        return cache[language]

    @property
    def display_product_name(self):
        return self.purchase_presentation.product_name

    @property
    def display_specification_chips(self):
        """Compatibility list derived from the canonical snapshot DTO."""

        return list(self.purchase_presentation.specification_chips)

    @property
    def purchased_sku(self):
        return self.purchase_presentation.variant_sku

    @property
    def order_history_thumbnail_url(self):
        """Return an optional current Product image for history recognition only.

        Purchase text always comes from ``purchase_presentation``.  This
        deliberately consults only the already-prepared Product relation (not
        Variant media) so an image is a zero-query, non-historical visual aid.
        """

        product = self._state.fields_cache.get("product")
        if product is None or not product.image:
            return ""
        return product.image.url

    @property
    def subtotal(self):
        return self.unit_price * self.quantity

    def __str__(self):
        return f"{self.quantity} × {self.product_name}"


class OrderStatusHistory(models.Model):
    """
    Tracks every order status transition so the customer
    can view a complete timeline.
    """

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="status_history",
    )

    status = models.CharField(
        max_length=20,
        choices=Order.Status.choices,
    )


    note = models.CharField(
        max_length=255,
        blank=True,
    )

    changed_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["changed_at"]

    @property
    def display_note(self):
        """Localize known system timeline notes without changing stored history."""

        note = self.note
        if note == "Order created, awaiting payment.":
            return gettext("Order created, awaiting payment.")
        if note == "Online payment confirmed.":
            return gettext("Online payment confirmed.")
        if note.startswith("Payment confirmed"):
            return gettext("Online payment confirmed.")
        if note == "COD order confirmed; payment is due on delivery.":
            return gettext("Cash on Delivery order confirmed; payment is due on delivery.")
        if note.startswith("Marked as shipped by "):
            return gettext("Order marked as shipped.")
        if note.startswith("Marked as delivered by "):
            return gettext("Order marked as delivered.")
        if note.startswith("Order placed on hold by "):
            return pgettext("order progress", "Order on hold")
        if note.startswith("Order resumed by "):
            return gettext("Order resumed.")
        if note.startswith("Cancelled:"):
            return gettext("Order cancelled.")
        return note

    def __str__(self):
        return f"Order #{self.order_id} → {self.get_status_display()}"



class OrderNote(models.Model):

    class Visibility(models.TextChoices):
        INTERNAL = "internal", _("Internal")
        CUSTOMER = "customer", _("Customer")

    class Category(models.TextChoices):
        GENERAL = "general", _("General")
        STATUS = "status", _("Status")
        SHIPPING = "shipping", _("Shipping")
        REFUND = "refund", _("Refund")
        PAYMENT = "payment", _("Payment")

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="notes",
    )

    author = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="order_notes",
    )

    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.INTERNAL,
    )

    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.GENERAL,
    )
    status = models.CharField(
        max_length=20,
        choices=Order.Status.choices,
        blank=True,
    )
    operation_code = models.CharField(
        max_length=100,
        blank=True,
    )

    message = models.TextField()

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["created_at"]

    @property
    def operation_display_label(self):
        """Human-readable localized label for stable operational codes."""

        labels = {
            "ONLINE_PAYMENT_RECORDED": gettext("Payment recorded"),
            "COD_PAYMENT_COLLECTED": gettext("Cash on Delivery payment collected"),
            "COD_CONFIRMED": gettext("Cash on Delivery order confirmed"),
            "SHIP_ORDER": gettext("Order shipped"),
            "DELIVER_ORDER": gettext("Order delivered"),
            "ORDER_ON_HOLD": gettext("Order placed on hold"),
            "ORDER_RESUMED": gettext("Order resumed"),
            "CANCEL_ORDER": gettext("Order cancelled"),
            "UNPAID_ORDER_CANCELLED": gettext("Unpaid order cancelled"),
            "STALE_ONLINE_PAYMENT_CANCELLED": gettext("Stale online payment cancelled"),
            "INVENTORY_RESTORED": gettext("Inventory restored"),
            "REFUND_REQUESTED": gettext("Refund requested"),
            "REFUND_APPROVED": gettext("Refund approved"),
            "REFUND_APPROVED_CANCELLED": gettext("Refund approved and order cancelled"),
            "REFUND_REJECTED": gettext("Refund rejected"),
            "REFUND_PROCESSED": gettext("Refund processed"),
        }
        return labels.get(self.operation_code, self.operation_code)

    @property
    def display_message(self):
        """Localize known system notes while preserving stored audit text."""

        if self.operation_code == "ONLINE_PAYMENT_RECORDED":
            return gettext("Payment confirmed via %(provider)s. Reference: %(reference)s.") % {
                "provider": self.order.payment_provider_display,
                "reference": self.order.payment_reference or "-",
            }
        if self.operation_code == "COD_PAYMENT_COLLECTED":
            return gettext("Cash on Delivery payment collected.")
        if self.operation_code == "COD_CONFIRMED":
            return gettext("Cash on Delivery order confirmed with payment due on delivery.")
        if self.operation_code == "SHIP_ORDER":
            return gettext("Order marked as shipped.")
        if self.operation_code == "DELIVER_ORDER":
            return gettext("Order marked as delivered.")
        if self.operation_code == "ORDER_ON_HOLD":
            return gettext("Order placed on hold.")
        if self.operation_code == "ORDER_RESUMED":
            return gettext("Order resumed.")
        if self.operation_code in {"CANCEL_ORDER", "UNPAID_ORDER_CANCELLED"}:
            return gettext("Order cancelled.")
        if self.operation_code == "STALE_ONLINE_PAYMENT_CANCELLED":
            return gettext("Stale online payment cancelled.")
        return self.message

    def __str__(self):
        return (
            f"Order #{self.order_id} - "
            f"{self.get_visibility_display()} Note"
        )
