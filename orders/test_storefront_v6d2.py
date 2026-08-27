from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import override
from io import BytesIO
from PIL import Image

from orders.forms import RefundRequestForm
from orders.models import Order, OrderItem
from orders.services import create_refund_request
from products.models import Product, ProductVariant


class CustomerRefundRequestV6D2Tests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("refund-owner", password="secret")
        self.other = get_user_model().objects.create_user("refund-other", password="secret")
        self.client.force_login(self.user)
        self.product = Product.objects.create(name="Refund product", slug="refund-product", sku="REFUND-P")
        self.variant = ProductVariant.objects.create(product=self.product, sku="REFUND-V", stock=6)

    def order(self, *, status=Order.Status.CONFIRMED, method=Order.PaymentMethod.MOCK, paid=True, delivered_at=None, user=None):
        order = Order.objects.create(user=user or self.user, user_order_number=31, full_name="Buyer", address="Historic street", city="Tehran", phone="123", status=status, payment_method=method, is_paid=paid, delivered_at=delivered_at, total_amount=Decimal("80"))
        OrderItem.objects.create(order=order, product=self.product, variant=self.variant, product_name="Historic Tile", unit_price=Decimal("80"), unit_reference_price=Decimal("100"), quantity=1, variant_snapshot={"sku": "REFUND-V", "size": "60x60 cm"})
        return order

    def image_upload(self, name="evidence.png"):
        image = Image.new("RGB", (1, 1), "white")
        payload = BytesIO()
        image.save(payload, format="PNG")
        return SimpleUploadedFile(name, payload.getvalue(), content_type="image/png")

    def test_eligible_refund_form_uses_account_shell_and_existing_fields(self):
        order = self.order()
        response = self.client.get(reverse("orders:order_cancel", args=[order.pk]))
        html = response.content.decode()
        self.assertContains(response, "account-shell")
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, "Request a refund for order #31")
        self.assertContains(response, "Refund reason")
        self.assertContains(response, "Additional information")
        self.assertContains(response, "maximum 10 MB per file")
        self.assertNotIn("OrderItem", html)
        self.assertNotIn("You save", html)

    def test_submission_status_and_duplicate_guard(self):
        order = self.order()
        url = reverse("orders:order_cancel", args=[order.pk])
        response = self.client.post(url, {"reason": Order.RefundReason.DAMAGED, "message": "Damaged corner"})
        self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.refund_status, Order.RefundStatus.REQUESTED)
        detail = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        self.assertContains(detail, "Your refund request is awaiting review.")
        self.assertContains(detail, "Damaged corner")
        repeat = self.client.post(url, {"reason": Order.RefundReason.OTHER, "message": "Again"})
        self.assertRedirects(repeat, reverse("orders:order_detail", args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.refund_message, "Damaged corner")

    def test_additional_information_is_required_by_form_and_service(self):
        for value in ("", "   \n  "):
            form = RefundRequestForm(data={"reason": Order.RefundReason.DAMAGED, "message": value})
            self.assertFalse(form.is_valid())
            self.assertEqual(form.errors["message"], ["Additional information is required."])

        order = self.order()
        with self.assertRaisesMessage(ValueError, "Additional information is required."):
            create_refund_request(
                order=order,
                actor=self.user,
                cleaned_data={"reason": Order.RefundReason.DAMAGED, "message": " \t "},
            )
        order.refresh_from_db()
        self.assertEqual(order.refund_status, Order.RefundStatus.NONE)

        with self.assertRaises(PermissionDenied):
            create_refund_request(
                order=order,
                actor=self.other,
                cleaned_data={"reason": Order.RefundReason.DAMAGED, "message": "Foreign submission"},
            )

        valid = RefundRequestForm(data={"reason": Order.RefundReason.DAMAGED, "message": "  Exact customer context  "})
        self.assertTrue(valid.is_valid())
        created = create_refund_request(order=order, actor=self.user, cleaned_data=valid.cleaned_data)
        self.assertEqual(created.refund_message, "Exact customer context")

    def test_legacy_blank_refund_message_remains_readable(self):
        order = self.order()
        order.refund_status = Order.RefundStatus.REQUESTED
        order.refund_reason = Order.RefundReason.OTHER
        order.refund_message = ""
        order.refund_requested_at = timezone.now()
        order.save(update_fields=["refund_status", "refund_reason", "refund_message", "refund_requested_at"])
        response = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your refund request is awaiting review.")

    def test_status_window_cod_cancelled_and_ownership_are_authoritative(self):
        delivered = self.order(status=Order.Status.DELIVERED, delivered_at=timezone.now() - timedelta(hours=1))
        self.assertContains(self.client.get(reverse("orders:order_detail", args=[delivered.pk])), "Refund requests are available until")
        unpaid_cod = self.order(method=Order.PaymentMethod.COD, paid=False)
        self.assertNotContains(self.client.get(reverse("orders:order_detail", args=[unpaid_cod.pk])), "Request refund")
        cancelled = self.order(status=Order.Status.CANCELLED)
        self.assertNotContains(self.client.get(reverse("orders:order_detail", args=[cancelled.pk])), "Request refund")
        foreign = self.order(user=self.other)
        self.assertEqual(self.client.get(reverse("orders:order_cancel", args=[foreign.pk])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("orders:order_cancel", args=[delivered.pk])).status_code, 302)

    def test_attachment_validation_and_farsi_refund_status(self):
        order = self.order()
        url = reverse("orders:order_cancel", args=[order.pk])
        bad = SimpleUploadedFile("evidence.txt", b"invalid", content_type="text/plain")
        invalid = self.client.post(url, {"reason": Order.RefundReason.OTHER, "message": "", "attachments": bad})
        self.assertContains(invalid, "Upload a valid JPG, PNG, WEBP, or PDF file.")
        self.assertEqual(order.refund_status, Order.RefundStatus.NONE)
        valid = self.image_upload()
        submitted = self.client.post(url, {"reason": Order.RefundReason.OTHER, "message": "شرح", "attachments": valid})
        self.assertRedirects(submitted, reverse("orders:order_detail", args=[order.pk]))
        attachment = order.refund_attachments.get()
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(reverse("orders:refund_attachment_download", args=[attachment.pk])).status_code,
            404,
        )
        self.client.force_login(self.user)
        with override("fa"):
            response = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        self.assertContains(response, "بازپرداخت")
        self.assertContains(response, "درخواست شما برای بازپرداخت")
        self.assertNotContains(response, ">Refund<")

    def test_detail_query_count_is_bounded_for_refund_attachments(self):
        order = self.order()
        order.refund_status = Order.RefundStatus.REQUESTED
        order.refund_requested_at = timezone.now()
        order.save(update_fields=["refund_status", "refund_requested_at"])
        url = reverse("orders:order_detail", args=[order.pk])
        self.client.get(url)
        with CaptureQueriesContext(connection) as queries:
            self.client.get(url)
        # Shared navigation performs the capability admission check once.
        self.assertLessEqual(len(queries), 15)
