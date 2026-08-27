from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import translation

from dashboard.notifications import get_dashboard_notifications
from dashboard.request_cache import REQUEST_CACHE_ATTRIBUTE
from dashboard.views.orders import build_queue_context
from orders.models import Order
from products.models import Product, ProductVariant
from support.models import Ticket, TicketCategory


class DashboardOperationalNotificationTests(TestCase):
    """Internal Dashboard alerts must remain separate from customer notices."""

    def setUp(self):
        self.language = translation.override("en")
        self.language.__enter__()
        call_command("seed_dashboard_roles", verbosity=0)
        self.product = Product.objects.create(
            name="Alert tile", slug="alert-tile", sku="ALERT-TILE", is_active=True
        )
        self.low_variant = ProductVariant.objects.create(
            product=self.product,
            sku="ALERT-LOW",
            stock=2,
            low_stock_threshold=5,
            is_active=True,
        )
        self.empty_variant = ProductVariant.objects.create(
            product=self.product,
            sku="ALERT-EMPTY",
            stock=0,
            low_stock_threshold=5,
            is_active=True,
        )
        self.customer = get_user_model().objects.create_user("alert-customer")
        self.order = Order.objects.create(
            user=self.customer,
            full_name="Alert customer",
            address="1 Alert Street",
            city="Tehran",
            phone="100",
            status=Order.Status.ON_HOLD,
        )
        category = TicketCategory.objects.create(name="Alert support")
        Ticket.objects.create(
            user=self.customer,
            category=category,
            subject="Alert ticket",
            message="Needs help",
        )

    def tearDown(self):
        self.language.__exit__(None, None, None)

    def role_user(self, role):
        user = get_user_model().objects.create_user(role.lower().replace(" ", "-"))
        user.groups.add(user.groups.model.objects.get(name=role))
        return user

    def test_inventory_conditions_have_exact_variant_management_links(self):
        notices = get_dashboard_notifications()
        low = next(item for item in notices if item["title"] == "Low stock")
        empty = next(item for item in notices if item["title"] == "Out of stock")
        self.assertEqual(
            low["url"],
            reverse("dashboard:products:variants:edit", args=[self.product.pk, self.low_variant.pk]),
        )
        self.assertEqual(
            empty["url"],
            reverse("dashboard:products:variants:edit", args=[self.product.pk, self.empty_variant.pk]),
        )

    def test_roles_only_receive_their_operational_notification_families(self):
        product_manager = self.role_user("Product Manager")
        order_manager = self.role_user("Order Manager")
        support_manager = self.role_user("Support Manager")
        analyst = self.role_user("Analyst")
        legacy_staff = get_user_model().objects.create_user("legacy-staff", is_staff=True)

        self.assertTrue(any("ALERT-LOW" in item["message"] for item in get_dashboard_notifications(product_manager)))
        self.assertFalse(any(item["title"] == "Orders on hold" for item in get_dashboard_notifications(product_manager)))
        self.assertTrue(any(item["title"] == "Orders on hold" for item in get_dashboard_notifications(order_manager)))
        self.assertFalse(any("ALERT-LOW" in item["message"] for item in get_dashboard_notifications(order_manager)))
        self.assertTrue(any(item["title"] == "Tickets needing response" for item in get_dashboard_notifications(support_manager)))
        self.assertEqual(get_dashboard_notifications(analyst), [])
        self.assertEqual(get_dashboard_notifications(legacy_staff), [])

    def test_product_manager_sees_dashboard_alert_bell_and_can_open_its_target(self):
        product_manager = self.role_user("Product Manager")
        self.client.force_login(product_manager)
        response = self.client.get(reverse("dashboard:home"))
        target = reverse(
            "dashboard:products:variants:edit", args=[self.product.pk, self.low_variant.pk]
        )
        self.assertContains(response, "Operational notifications")
        self.assertContains(response, target)
        self.assertEqual(self.client.get(target).status_code, 200)

        with translation.override("fa"):
            persian_response = self.client.get(reverse("dashboard:home"))
        self.assertContains(persian_response, "اعلان‌های عملیاتی")
        self.assertNotContains(persian_response, "Operational notifications")

    def test_inventory_attention_card_uses_the_same_variant_alerts_as_the_feed(self):
        product_manager = self.role_user("Product Manager")
        self.client.force_login(product_manager)
        response = self.client.get(reverse("dashboard:home"))
        low_url = reverse(
            "dashboard:products:variants:edit", args=[self.product.pk, self.low_variant.pk]
        )
        empty_url = reverse(
            "dashboard:products:variants:edit", args=[self.product.pk, self.empty_variant.pk]
        )
        feed_urls = {item["url"] for item in get_dashboard_notifications(product_manager)}

        self.assertEqual(response.context["low_stock_count"], 1)
        self.assertEqual(response.context["out_of_stock_count"], 1)
        self.assertContains(response, low_url)
        self.assertContains(response, empty_url)
        self.assertIn(low_url, feed_urls)
        self.assertIn(empty_url, feed_urls)
        html = response.content.decode()
        action_index = html.index('href="#inventory-alerts"')
        card_start = html.rfind("<section", 0, action_index)
        card_end = html.index("</section>", action_index) + len("</section>")
        card_html = html[card_start:card_end]
        self.assertIn('href="#inventory-alerts"', card_html)
        self.assertNotIn(
            f'href="{reverse("dashboard:products:catalog")}"',
            card_html,
        )

    def test_inventory_attention_card_has_an_explicit_healthy_zero_state(self):
        self.low_variant.delete()
        self.empty_variant.delete()
        product_manager = self.role_user("Product Manager")
        self.client.force_login(product_manager)
        response = self.client.get(reverse("dashboard:home"))

        self.assertEqual(response.context["low_stock_count"], 0)
        self.assertEqual(response.context["out_of_stock_count"], 0)
        self.assertContains(response, "Inventory is healthy.")
        self.assertContains(response, "No low-stock or out-of-stock variants.")

    def test_filtered_queue_count_is_not_reused_as_the_global_sidebar_count(self):
        request = RequestFactory().get("/dashboard/orders/", {"status": Order.Status.ON_HOLD})
        request.user = self.role_user("Order Manager")

        build_queue_context(
            request,
            Order.objects.all(),
            count_cache_key=("orders", "attention", request.user.pk),
        )

        self.assertFalse(hasattr(request, REQUEST_CACHE_ATTRIBUTE))
