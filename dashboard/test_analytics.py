from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone, translation

from dashboard.selectors.analytics import resolve_analytics_period
from dashboard.selectors.analytics import get_financial_metrics
from dashboard.selectors.analytics_exports import write_orders_csv
from dashboard.management.commands.seed_dashboard_roles import Command as SeedDashboardRolesCommand
from orders.models import Order


class AnalyticsSmokeTests(TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(translation.override("en"))
        SeedDashboardRolesCommand().handle()
        self.staff = User.objects.create_user("analyst", password="test-pass")
        self.staff.groups.add(Group.objects.get(name="Analyst"))
        self.owner = User.objects.create_superuser("owner", "owner@example.test", "test-pass")

    def test_empty_analytics_page_renders(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dashboard:analytics"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Business Analytics")

    def test_custom_period_and_previous_period_are_equal_length(self):
        period = resolve_analytics_period({"range": "custom", "start": "2026-01-10", "end": "2026-01-14"})
        self.assertEqual(period.code, "custom")
        self.assertEqual(period.end - period.start, period.previous_end - period.previous_start)

    def test_invalid_custom_range_falls_back_gracefully(self):
        period = resolve_analytics_period({"range": "custom", "start": "2026-02-10", "end": "2026-01-14"}, now=timezone.now())
        self.assertEqual(period.code, "30d")

    def test_financial_metrics_do_not_combine_currencies(self):
        now = timezone.now()
        common = {"user": self.staff, "full_name": "Analyst", "address": "Address", "city": "City", "phone": "123"}
        Order.objects.create(**common, total_amount=Decimal("20.00"), currency="USD", is_paid=True, paid_at=now)
        Order.objects.create(**common, total_amount=Decimal("3000000.00"), currency="IRT", is_paid=True, paid_at=now)
        rows = {row["currency"]: row for row in get_financial_metrics(resolve_analytics_period({"range": "today"}, now=now))["by_currency"]}
        self.assertEqual(rows["USD"]["gross_sales"]["value"], Decimal("20.00"))
        self.assertEqual(rows["IRT"]["gross_sales"]["value"], Decimal("3000000.00"))

    def test_order_csv_includes_currency(self):
        now = timezone.now()
        Order.objects.create(user=self.staff, full_name="Analyst", address="Address", city="City", phone="123", total_amount=Decimal("20.00"), currency="USD", is_paid=True, paid_at=now)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dashboard:analytics_export_orders_csv"))
        self.assertContains(response, "Currency")
        self.assertContains(response, "USD")

    def test_analytics_labels_localize_without_changing_financial_metrics(self):
        now = timezone.now()
        common = {"user": self.staff, "full_name": "Analyst", "address": "Address", "city": "City", "phone": "123"}
        Order.objects.create(**common, total_amount=Decimal("20.00"), currency="USD", is_paid=True, paid_at=now)
        Order.objects.create(**common, total_amount=Decimal("3000000.00"), currency="IRT", is_paid=True, paid_at=now)
        self.client.force_login(self.staff)

        with translation.override("en"):
            english = self.client.get(reverse("dashboard:analytics", args=[]))
            english_metrics = get_financial_metrics(resolve_analytics_period({"range": "today"}, now=now))
        with translation.override("fa"):
            persian = self.client.get(reverse("dashboard:analytics", args=[]))
            persian_metrics = get_financial_metrics(resolve_analytics_period({"range": "today"}, now=now))

        self.assertContains(english, "Business Analytics")
        self.assertContains(persian, "تحلیل‌های کسب‌وکار")
        self.assertContains(persian, 'dir="rtl"')
        self.assertEqual(english_metrics["by_currency"], persian_metrics["by_currency"])
