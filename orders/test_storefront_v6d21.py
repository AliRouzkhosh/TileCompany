from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override

from dashboard.services import approve_refund, request_refund_information, reject_refund
from dashboard.selectors.orders import (
    get_dashboard_statistics,
    get_fulfillment_queue,
    get_refund_customer_replied_queue,
    get_refund_review_queue,
    get_refund_waiting_customer_queue,
)
from notifications.models import Notification, NotificationEvent
from notifications.presentation import get_notification_presentation
from orders.models import Order, OrderItem
from products.models import Product, ProductVariant
from support.models import Ticket, TicketActivity, TicketStatus
from support.taxonomy import REFUND_CATEGORY_SLUG


class RefundClarificationWorkflowV6D21Tests(TestCase):
    def setUp(self):
        self.customer = get_user_model().objects.create_user("refund-customer", password="secret")
        self.other_customer = get_user_model().objects.create_user("other-customer", password="secret")
        # Refund decisions are Owner-only in the final F3H policy.  This actor
        # also creates the linked internal Support reply through that workflow.
        self.staff = get_user_model().objects.create_superuser("refund-owner", "refund-owner@example.test", "secret")
        self.product = Product.objects.create(name="Generic purchased product", slug="generic-purchased-product", sku="REFUND-POLISH")
        self.variant = ProductVariant.objects.create(product=self.product, sku="REFUND-POLISH-V", stock=8)

    def refund_order(self, *, status=Order.Status.DELIVERED, refund_status=Order.RefundStatus.REQUESTED):
        order = Order.objects.create(
            user=self.customer,
            user_order_number=23,
            full_name="Refund customer",
            address="Historical address",
            city="Tehran",
            phone="123",
            status=status,
            payment_method=Order.PaymentMethod.MOCK,
            is_paid=True,
            delivered_at=timezone.now() if status == Order.Status.DELIVERED else None,
            total_amount=Decimal("80"),
            refund_status=refund_status,
            refund_reason=Order.RefundReason.DAMAGED,
            refund_message="The product arrived with damage.",
            refund_requested_at=timezone.now(),
            refund_requested_stage=status,
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            variant=self.variant,
            product_name="Historical generic product",
            quantity=1,
            unit_price=Decimal("80"),
            unit_reference_price=Decimal("100"),
            variant_snapshot={"sku": "REFUND-POLISH-V", "configuration": "Historic configuration"},
        )
        return order

    def test_clarification_creates_one_owned_ticket_and_meaningful_notifications(self):
        order = self.refund_order()

        self.assertTrue(request_refund_information(order, self.staff, "Please share a photo of the damaged item."))
        order.refresh_from_db()
        ticket = order.refund_support_ticket
        self.assertEqual(order.status, Order.Status.DELIVERED)
        self.assertEqual(order.refund_status, Order.RefundStatus.NEEDS_INFORMATION)
        self.assertEqual(order.refund_response, "Please share a photo of the damaged item.")
        self.assertEqual(ticket.user_id, self.customer.id)
        self.assertEqual(ticket.order_id, order.id)
        self.assertEqual(ticket.category.slug, REFUND_CATEGORY_SLUG)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.WAITING_CUSTOMER)
        self.assertEqual(ticket.replies.count(), 1)
        self.assertEqual(
            ticket.message,
            "Refund request\n\nReason:\nProduct arrived damaged\n\nAdditional information:\nThe product arrived with damage.",
        )
        self.assertNotIn("being reviewed", ticket.message)
        self.assertEqual(
            ticket.activities.get(event_type=TicketActivity.Event.CREATED).actor_id,
            self.customer.id,
        )

        notice = Notification.objects.get(event_type=NotificationEvent.REFUND_NEEDS_INFORMATION)
        self.assertEqual(notice.user_id, self.customer.id)
        self.assertEqual(notice.target_url, reverse("support:ticket_detail", args=[ticket.id]))
        presentation = get_notification_presentation(notice)
        self.assertEqual(presentation.title, "More information required for your refund request")
        self.assertEqual(presentation.action_label, "Provide details")

        self.assertTrue(request_refund_information(order, self.staff, "Please also include the package label."))
        order.refresh_from_db()
        self.assertEqual(Ticket.objects.filter(refund_order=order).count(), 1)
        self.assertEqual(order.refund_support_ticket.replies.count(), 2)
        self.assertEqual(
            order.refund_support_ticket.message,
            "Refund request\n\nReason:\nProduct arrived damaged\n\nAdditional information:\nThe product arrived with damage.",
        )
        self.assertEqual(Notification.objects.filter(event_type=NotificationEvent.REFUND_NEEDS_INFORMATION).count(), 2)

    def test_customer_surface_ticket_ownership_and_reply_do_not_resolve_refund(self):
        order = self.refund_order()
        request_refund_information(order, self.staff, "Please upload a clear photo.")
        order.refresh_from_db()
        ticket = order.refund_support_ticket
        self.assertIn(order, get_refund_review_queue())
        self.assertIn(order, get_refund_waiting_customer_queue())
        self.assertNotIn(order, get_refund_customer_replied_queue())
        self.client.force_login(self.customer)

        detail = self.client.get(reverse("orders:order_detail", args=[order.id]))
        self.assertContains(detail, "More information required")
        self.assertContains(detail, "Provide details")
        self.assertContains(detail, reverse("support:ticket_detail", args=[ticket.id]))

        with override("fa"):
            persian_detail = self.client.get(reverse("orders:order_detail", args=[order.id]))
        self.assertContains(persian_detail, "اطلاعات بیشتری نیاز است")
        self.assertContains(persian_detail, "ارسال اطلاعات")
        self.assertNotContains(persian_detail, ">More information required<")

        reply = self.client.post(reverse("support:ticket_detail", args=[ticket.id]), {"message": "I have attached the requested photo."})
        self.assertRedirects(reply, reverse("support:ticket_detail", args=[ticket.id]))
        ticket.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(ticket.status, TicketStatus.WAITING_SUPPORT)
        self.assertEqual(order.refund_status, Order.RefundStatus.NEEDS_INFORMATION)
        self.assertIn(order, get_refund_review_queue())
        self.assertIn(order, get_refund_customer_replied_queue())
        self.assertNotIn(order, get_refund_waiting_customer_queue())

        detail = self.client.get(reverse("orders:order_detail", args=[order.id]))
        self.assertContains(detail, "Information received")
        self.assertContains(detail, "Your refund request is being reviewed.")
        self.assertNotContains(detail, ">Provide details</a>")

        original_ticket_id = ticket.id
        self.assertTrue(request_refund_information(order, self.staff, "Please also show the shipping label."))
        order.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(order.refund_support_ticket_id, original_ticket_id)
        self.assertEqual(order.refund_status, Order.RefundStatus.NEEDS_INFORMATION)
        self.assertEqual(ticket.status, TicketStatus.WAITING_CUSTOMER)
        self.assertEqual(order.status, Order.Status.DELIVERED)

        self.client.force_login(self.other_customer)
        self.assertEqual(self.client.get(reverse("support:ticket_detail", args=[ticket.id])).status_code, 404)

    def test_order_history_and_top_status_surface_show_each_refund_state(self):
        expected = {
            Order.RefundStatus.REQUESTED: ("Refund requested", "Your refund request is under review."),
            Order.RefundStatus.NEEDS_INFORMATION: ("More information required", "Support needs more information to continue your refund request."),
            Order.RefundStatus.APPROVED: ("Refund approved", "Your refund request has been approved and is awaiting processing."),
            Order.RefundStatus.REJECTED: ("Refund rejected", "Your refund request was reviewed and rejected."),
            Order.RefundStatus.PROCESSED: ("Refund processed", "Your refund has been processed."),
        }
        orders = []
        for index, refund_status in enumerate(expected, start=1):
            order = self.refund_order(refund_status=refund_status)
            order.user_order_number = 100 + index
            order.save(update_fields=["user_order_number"])
            orders.append(order)
        non_refund_order = self.refund_order(refund_status=Order.RefundStatus.NONE)
        non_refund_order.user_order_number = 200
        non_refund_order.save(update_fields=["user_order_number"])
        self.client.force_login(self.customer)

        history = self.client.get(reverse("orders:order_list"), {"status": "refund"})
        self.assertEqual(history.status_code, 200)
        for refund_status, (label, _) in expected.items():
            if refund_status == Order.RefundStatus.REJECTED:
                continue
            self.assertContains(history, label)
        self.assertNotContains(history, 'account-order-card__refund-badge--none')

        for order, (label, helper) in zip(orders, expected.values()):
            with self.subTest(refund_status=order.refund_status):
                detail = self.client.get(reverse("orders:order_detail", args=[order.pk]))
                self.assertContains(detail, "Delivered")
                self.assertContains(detail, label)
                self.assertContains(detail, helper)

        non_refund_detail = self.client.get(reverse("orders:order_detail", args=[non_refund_order.pk]))
        self.assertContains(non_refund_detail, "Delivered")
        self.assertNotContains(non_refund_detail, 'order-status-surface__refund')

    def test_refund_visibility_is_localized_in_order_history_and_detail(self):
        order = self.refund_order(refund_status=Order.RefundStatus.REQUESTED)
        self.client.force_login(self.customer)

        with override("fa"):
            history = self.client.get(reverse("orders:order_list"), {"status": "refund"})
            detail = self.client.get(reverse("orders:order_detail", args=[order.pk]))

        self.assertContains(history, "بازپرداخت درخواست شد")
        self.assertContains(detail, "بازپرداخت درخواست شد")
        self.assertContains(detail, "درخواست بازپرداخت شما در حال بررسی است.")
        self.assertNotContains(detail, ">Refund requested<")

    def test_closed_conversation_is_reopened_and_reused_for_another_round(self):
        order = self.refund_order()
        request_refund_information(order, self.staff, "Please upload the receipt.")
        order.refresh_from_db()
        ticket = order.refund_support_ticket
        ticket.status = TicketStatus.CLOSED
        ticket.save(update_fields=["status"])

        self.assertTrue(request_refund_information(order, self.staff, "Please also show the package label."))
        order.refresh_from_db()
        ticket.refresh_from_db()

        self.assertEqual(order.refund_support_ticket_id, ticket.id)
        self.assertEqual(Ticket.objects.filter(refund_order=order).count(), 1)
        self.assertEqual(ticket.status, TicketStatus.WAITING_CUSTOMER)
        self.assertEqual(ticket.replies.count(), 2)

    def test_forged_cross_owner_refund_ticket_is_rejected(self):
        order = self.refund_order()
        foreign_order = self.refund_order()
        foreign_order.user = self.other_customer
        foreign_order.save(update_fields=["user"])
        request_refund_information(foreign_order, self.staff, "Foreign request")
        foreign_order.refresh_from_db()
        foreign_ticket = foreign_order.refund_support_ticket
        foreign_order.refund_support_ticket = None
        foreign_order.save(update_fields=["refund_support_ticket"])
        order.refund_support_ticket = foreign_ticket
        order.save(update_fields=["refund_support_ticket"])

        with self.assertRaisesMessage(ValueError, "does not belong to this order"):
            request_refund_information(order, self.staff, "Please provide more details.")

        order.refresh_from_db()
        self.assertEqual(order.refund_status, Order.RefundStatus.REQUESTED)

    def test_staff_requires_customer_safe_question_and_can_finish_review_after_reply(self):
        order = self.refund_order(status=Order.Status.CONFIRMED)
        self.client.force_login(self.staff)
        url = reverse("dashboard:request_refund_information", args=[order.id])
        invalid = self.client.post(url, {"refund_response": ""})
        self.assertContains(invalid, "This field is required.")
        self.assertEqual(order.refund_status, Order.RefundStatus.REQUESTED)

        asked = self.client.post(url, {"refund_response": "Please provide a photo."})
        self.assertRedirects(asked, reverse("dashboard:order_detail", args=[order.id]))
        order.refresh_from_db()
        self.assertEqual(order.refund_status, Order.RefundStatus.NEEDS_INFORMATION)
        self.assertEqual(order.status, Order.Status.CONFIRMED)
        self.assertTrue(approve_refund(order, self.staff, "Approved after review."))
        order.refresh_from_db()
        self.assertEqual(order.refund_status, Order.RefundStatus.APPROVED)

        rejected = self.refund_order()
        request_refund_information(rejected, self.staff, "Please provide a photo.")
        self.assertTrue(reject_refund(rejected, self.staff, "The evidence does not support the request."))
        rejected.refresh_from_db()
        self.assertEqual(rejected.refund_status, Order.RefundStatus.REJECTED)

        confirmed_rejection = self.refund_order(status=Order.Status.CONFIRMED)
        request_refund_information(confirmed_rejection, self.staff, "Please provide evidence.")
        self.assertTrue(reject_refund(confirmed_rejection, self.staff, "Not supported by the evidence."))
        confirmed_rejection.refresh_from_db()
        self.assertEqual(confirmed_rejection.status, Order.Status.CONFIRMED)
        self.assertIn(confirmed_rejection, get_fulfillment_queue())

    def test_dashboard_subqueues_counts_and_query_params_use_persisted_truth(self):
        requested = self.refund_order(status=Order.Status.CONFIRMED)
        waiting = self.refund_order(status=Order.Status.CONFIRMED)
        replied = self.refund_order(status=Order.Status.CONFIRMED)
        for number, order in enumerate((requested, waiting, replied), start=101):
            order.user_order_number = number
            order.save(update_fields=["user_order_number"])
        request_refund_information(waiting, self.staff, "Please provide the package label.")
        request_refund_information(replied, self.staff, "Please provide a clear photo.")
        replied.refresh_from_db()
        self.client.force_login(self.customer)
        self.client.post(
            reverse("support:ticket_detail", args=[replied.refund_support_ticket_id]),
            {"message": "Here is the requested context."},
        )

        self.assertEqual(get_refund_review_queue().filter(pk__in=[requested.pk, waiting.pk, replied.pk]).count(), 3)
        self.assertEqual(get_refund_waiting_customer_queue().filter(pk__in=[requested.pk, waiting.pk, replied.pk]).count(), 1)
        self.assertEqual(get_refund_customer_replied_queue().filter(pk__in=[requested.pk, waiting.pk, replied.pk]).count(), 1)
        self.assertFalse(get_fulfillment_queue().filter(pk__in=[requested.pk, waiting.pk, replied.pk]).exists())
        stats = get_dashboard_statistics()
        self.assertEqual(stats["refund_review_queue"], 3)
        self.assertEqual(stats["refund_waiting_customer_queue"], 1)
        self.assertEqual(stats["refund_customer_replied_queue"], 1)

        self.client.force_login(self.staff)
        queue = self.client.get(reverse("dashboard:refund_queue"))
        self.assertContains(queue, "Waiting for customer")
        self.assertContains(queue, "Customer replied")
        filtered = self.client.get(reverse("dashboard:refund_queue"), {"refund_state": "customer_replied"})
        self.assertContains(filtered, f"#{replied.user_order_number}")
        self.assertNotContains(filtered, f"#{waiting.user_order_number}")
        forged = self.client.get(reverse("dashboard:refund_queue"), {"refund_state": "approved"})
        self.assertEqual(forged.status_code, 200)
        for order in (requested, waiting, replied):
            order.refresh_from_db()
            self.assertIn(order.refund_status, {Order.RefundStatus.REQUESTED, Order.RefundStatus.NEEDS_INFORMATION})

    def test_shared_upload_and_refund_breadcrumb_are_rendered(self):
        order = self.refund_order(status=Order.Status.CONFIRMED, refund_status=Order.RefundStatus.NONE)
        self.client.force_login(self.customer)
        response = self.client.get(reverse("orders:order_cancel", args=[order.id]))
        self.assertContains(response, "data-file-upload")
        self.assertContains(response, "file-upload__trigger")
        self.assertContains(response, "data-file-upload-remove")
        self.assertNotContains(response, "No file chosen")
        self.assertContains(response, "refund-request__help")
        self.assertContains(response, "file-upload__help")
        self.assertContains(response, reverse("accounts:overview"))
        self.assertContains(response, reverse("orders:order_list"))
        self.assertContains(response, reverse("orders:order_detail", args=[order.id]))
        self.assertContains(response, "Refund request")

        ticket_create = self.client.get(reverse("support:ticket_create"))
        self.assertContains(ticket_create, "data-file-upload")

        with override("fa"):
            persian_upload = self.client.get(reverse("orders:order_cancel", args=[order.id]))
        self.assertContains(persian_upload, "انتخاب فایل‌ها")
        self.assertContains(persian_upload, "حذف فایل‌های انتخاب‌شده")
        self.assertContains(persian_upload, "لطفاً مشکل و هر اطلاعاتی را که به بررسی درخواست شما کمک می‌کند شرح دهید.", count=2)
        self.assertNotContains(persian_upload, "Please describe the problem and any information that will help us review your request.")
        self.assertNotContains(persian_upload, ">Choose files<")

        order.refund_status = Order.RefundStatus.REQUESTED
        order.save(update_fields=["refund_status"])
        request_refund_information(order, self.staff, "Please provide a supporting photo.")
        order.refresh_from_db()
        ticket_detail = self.client.get(reverse("support:ticket_detail", args=[order.refund_support_ticket_id]))
        self.assertContains(ticket_detail, "data-file-upload")
