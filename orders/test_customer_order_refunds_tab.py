from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.translation import override

from orders.models import Order
from orders.selectors import get_customer_order_history


class CustomerOrderRefundsTabTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("refund-tabs", password="secret")
        self.client.force_login(self.user)
        self._number = 100

    def order(self, *, status, refund_status=Order.RefundStatus.NONE):
        self._number += 1
        return Order.objects.create(
            user=self.user,
            user_order_number=self._number,
            full_name="Refund tabs customer",
            address="Historical address",
            city="Tehran",
            phone="123",
            status=status,
            payment_method=Order.PaymentMethod.MOCK,
            is_paid=True,
            total_amount=Decimal("50"),
            refund_status=refund_status,
        )

    def bucket_ids(self, status):
        history = get_customer_order_history(user=self.user, status_filter=status)
        return {order.pk for order in history["page_obj"].object_list}

    def test_refund_statuses_are_exclusive_and_counts_partition_customer_orders(self):
        requested = self.order(status=Order.Status.CONFIRMED, refund_status=Order.RefundStatus.REQUESTED)
        needs_information = self.order(status=Order.Status.CONFIRMED, refund_status=Order.RefundStatus.NEEDS_INFORMATION)
        approved = self.order(status=Order.Status.DELIVERED, refund_status=Order.RefundStatus.APPROVED)
        processed = self.order(status=Order.Status.DELIVERED, refund_status=Order.RefundStatus.PROCESSED)
        rejected_delivered = self.order(status=Order.Status.DELIVERED, refund_status=Order.RefundStatus.REJECTED)
        rejected_current = self.order(status=Order.Status.CONFIRMED, refund_status=Order.RefundStatus.REJECTED)
        delivered = self.order(status=Order.Status.DELIVERED)
        current = self.order(status=Order.Status.CONFIRMED)
        cancelled = self.order(status=Order.Status.CANCELLED)

        buckets = {key: self.bucket_ids(key) for key in ("current", "delivered", "refund", "cancelled")}
        self.assertEqual(buckets["refund"], {requested.pk, needs_information.pk, approved.pk, processed.pk})
        self.assertEqual(buckets["delivered"], {rejected_delivered.pk, delivered.pk})
        self.assertEqual(buckets["current"], {rejected_current.pk, current.pk})
        self.assertEqual(buckets["cancelled"], {cancelled.pk})

        all_bucket_ids = [order_id for ids in buckets.values() for order_id in ids]
        self.assertEqual(len(all_bucket_ids), len(set(all_bucket_ids)))
        self.assertEqual(set(all_bucket_ids), {order.pk for order in (requested, needs_information, approved, processed, rejected_delivered, rejected_current, delivered, current, cancelled)})

        history = get_customer_order_history(user=self.user, status_filter="refund")
        self.assertEqual(history["filter_counts"], {"current": 2, "delivered": 2, "refund": 4, "cancelled": 1})

    def test_refund_tab_reuses_badges_and_rejected_refunds_remain_in_lifecycle_tabs(self):
        requested = self.order(status=Order.Status.CONFIRMED, refund_status=Order.RefundStatus.REQUESTED)
        rejected = self.order(status=Order.Status.DELIVERED, refund_status=Order.RefundStatus.REJECTED)

        refund_response = self.client.get(reverse("orders:order_list"), {"status": "refund"})
        self.assertContains(refund_response, "Refunds")
        self.assertContains(refund_response, "Refund requested")
        self.assertContains(refund_response, f"Order #{requested.user_order_number}")
        self.assertNotContains(refund_response, f"Order #{rejected.user_order_number}")

        delivered_response = self.client.get(reverse("orders:order_list"), {"status": "delivered"})
        self.assertContains(delivered_response, "Refund rejected")
        self.assertContains(delivered_response, f"Order #{rejected.user_order_number}")

    def test_refund_empty_state_pagination_and_htmx_preserve_filter_contract(self):
        empty = self.client.get(reverse("orders:order_list"), {"status": "refund"})
        self.assertContains(empty, "No refund requests")
        self.assertContains(empty, "Orders with an active or completed refund will appear here.")

        for _ in range(11):
            self.order(status=Order.Status.CONFIRMED, refund_status=Order.RefundStatus.REQUESTED)

        page_two = self.client.get(reverse("orders:order_list"), {"status": "refund", "page": 2})
        self.assertEqual(page_two.context["selected_filter"], "refund")
        self.assertContains(page_two, "?status=refund&page=1")

        htmx = self.client.get(
            reverse("orders:order_list"),
            {"status": "refund", "page": 2},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="orders-workspace",
        )
        self.assertContains(htmx, 'id="orders-workspace"')
        self.assertNotContains(htmx, "<!DOCTYPE html>")
        self.assertContains(htmx, 'hx-push-url="true"')
        self.assertContains(htmx, "?status=refund&page=1")

    def test_refund_tab_is_localized_and_history_queries_remain_bounded(self):
        requested = self.order(status=Order.Status.CONFIRMED, refund_status=Order.RefundStatus.REQUESTED)
        with override("fa"):
            response = self.client.get(reverse("orders:order_list"), {"status": "refund"})
        self.assertContains(response, "بازپرداخت‌ها")
        self.assertContains(response, "بازپرداخت درخواست شد")
        self.assertNotContains(response, ">Refunds<")

        small_user = get_user_model().objects.create_user("refund-tabs-small", password="secret")
        Order.objects.create(
            user=small_user, user_order_number=1, full_name="Small", address="Address", city="Tehran", phone="123",
            status=Order.Status.CONFIRMED, payment_method=Order.PaymentMethod.MOCK, is_paid=True,
            total_amount=Decimal("10"), refund_status=Order.RefundStatus.REQUESTED,
        )

        def query_count(user):
            with CaptureQueriesContext(connection) as queries:
                history = get_customer_order_history(user=user, status_filter="refund")
                list(history["page_obj"].object_list)
            return len(queries)

        self.assertEqual(query_count(small_user), query_count(self.user))
        self.assertLessEqual(query_count(self.user), 4)
        self.assertIn(requested.pk, self.bucket_ids("refund"))
