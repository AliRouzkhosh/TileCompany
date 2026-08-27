from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from orders.models import Order


class OrderAuthorizationTests(TestCase):
    def setUp(self):
        self.language = translation.override("en")
        self.language.__enter__()
        call_command("seed_dashboard_roles", verbosity=0)
        User = get_user_model()
        self.order_manager = User.objects.create_user("order-manager", password="secret")
        self.order_manager.groups.add(Group.objects.get(name="Order Manager"))
        self.viewer = User.objects.create_user("order-viewer", password="secret")
        for codename in ("view_order", "view_orderitem", "view_orderstatushistory", "view_ordernote"):
            self.viewer.user_permissions.add(Permission.objects.get(content_type__app_label="orders", codename=codename))
        self.product_manager = User.objects.create_user("product-manager", password="secret")
        self.product_manager.groups.add(Group.objects.get(name="Product Manager"))
        self.legacy_staff = User.objects.create_user("legacy-staff", password="secret", is_staff=True)
        self.customer = User.objects.create_user("customer", password="secret")
        self.refund_permission_user = User.objects.create_user("refund-permission", password="secret")
        self.refund_permission_user.user_permissions.add(
            Permission.objects.get(content_type__app_label="orders", codename="process_refund")
        )
        self.owner = User.objects.create_superuser("owner", "owner@example.com", "secret")
        self.order = self.make_order(status=Order.Status.CONFIRMED, is_paid=True)

    def tearDown(self):
        self.language.__exit__(None, None, None)

    def make_order(self, *, status, is_paid=False):
        return Order.objects.create(
            user=self.customer,
            full_name="Fulfillment Customer",
            address="1 Tile Street",
            city="Tehran",
            phone="123456",
            status=status,
            is_paid=is_paid,
        )

    def test_order_manager_non_staff_has_read_queues_and_no_refunds(self):
        self.assertFalse(self.order_manager.is_staff)
        self.client.force_login(self.order_manager)
        for name in ("fulfillment_queue", "in_transit_queue", "attention_queue", "all_orders"):
            self.assertEqual(self.client.get(reverse(f"dashboard:{name}")).status_code, 200)
        detail = self.client.get(reverse("dashboard:order_detail", args=[self.order.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Payment reference")
        self.assertNotContains(detail, "Supporting files")
        for name in ("refund_queue", "refund_processing_queue"):
            self.assertEqual(self.client.get(reverse(f"dashboard:{name}")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:approve_refund", args=[self.order.pk])).status_code, 403)

    def test_order_manager_can_transition_and_cancel_without_product_permissions(self):
        self.client.force_login(self.order_manager)
        self.assertEqual(self.client.post(reverse("dashboard:ship_order", args=[self.order.pk])).status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.SHIPPED)

        pending = self.make_order(status=Order.Status.CONFIRMED, is_paid=False)
        pending.payment_method = Order.PaymentMethod.COD
        pending.save(update_fields=["payment_method"])
        self.assertEqual(self.client.post(reverse("dashboard:cancel_order", args=[pending.pk])).status_code, 302)
        pending.refresh_from_db()
        self.assertEqual(pending.status, Order.Status.CANCELLED)
        self.assertFalse(self.order_manager.has_perm("products.change_inventory"))

    def test_read_only_viewer_cannot_mutate_and_bulk_refund_is_blocked(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("dashboard:order_detail", args=[self.order.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("dashboard:ship_order", args=[self.order.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("dashboard:bulk_action"), {"action": "ship", "orders": [self.order.pk]}).status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CONFIRMED)

    def test_bulk_actions_are_authorized_per_action(self):
        self.client.force_login(self.order_manager)
        self.assertEqual(self.client.post(reverse("dashboard:bulk_action"), {"action": "ship", "orders": [self.order.pk]}).status_code, 302)
        other = self.make_order(status=Order.Status.CONFIRMED, is_paid=True)
        self.assertEqual(self.client.post(reverse("dashboard:bulk_action"), {"action": "process_refund", "orders": [other.pk]}).status_code, 403)
        self.assertEqual(self.client.post(reverse("dashboard:bulk_action"), {"action": "unknown", "orders": [other.pk]}).status_code, 403)

    def test_product_manager_legacy_staff_customer_and_manual_refund_permission_are_denied(self):
        url = reverse("dashboard:all_orders")
        for user in (self.product_manager, self.legacy_staff, self.customer):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.refund_permission_user)
        self.assertEqual(self.client.post(reverse("dashboard:process_refund", args=[self.order.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:refund_queue")).status_code, 403)
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_owner_keeps_refund_and_sensitive_payment_access(self):
        self.client.force_login(self.owner)
        detail = self.client.get(reverse("dashboard:order_detail", args=[self.order.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Payment reference")
        self.assertEqual(self.client.get(reverse("dashboard:refund_queue")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:approve_refund", args=[self.order.pk])).status_code, 200)

    def test_order_sidebar_is_permission_aware(self):
        self.client.force_login(self.order_manager)
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, "Fulfillment")
        self.assertContains(response, "In Transit")
        self.assertContains(response, "Attention")
        self.assertContains(response, "All Orders")
        self.assertNotContains(response, "Refund Processing")
        self.assertNotContains(response, "Catalog Schema")
