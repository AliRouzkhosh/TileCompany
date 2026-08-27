from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from dashboard.management.commands.seed_dashboard_roles import Command as SeedDashboardRolesCommand


class AnalyticsAuthorizationTests(TestCase):
    def setUp(self):
        SeedDashboardRolesCommand().handle()
        User = get_user_model()
        self.analyst = User.objects.create_user("analyst", password="secret")
        self.analyst.groups.add(Group.objects.get(name="Analyst"))
        self.product = User.objects.create_user("product", password="secret")
        self.product.groups.add(Group.objects.get(name="Product Manager"))
        self.legacy = User.objects.create_user("legacy", password="secret", is_staff=True)
        self.owner = User.objects.create_superuser("owner", "owner@example.test", "secret")

    def test_analytics_and_export_policy(self):
        self.client.force_login(self.analyst)
        self.assertFalse(self.analyst.is_staff)
        self.assertEqual(self.client.get(reverse("dashboard:analytics")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:analytics_export_products_csv")).status_code, 200)
        for name in ("analytics_export_orders_csv", "analytics_export_refunds_csv", "analytics_export_customers_csv", "analytics_export_report_xlsx"):
            self.assertEqual(self.client.get(reverse(f"dashboard:{name}")).status_code, 403)
        self.client.force_login(self.owner)
        for name in ("analytics", "analytics_export_orders_csv", "analytics_export_refunds_csv", "analytics_export_products_csv", "analytics_export_customers_csv", "analytics_export_report_xlsx"):
            self.assertEqual(self.client.get(reverse(f"dashboard:{name}")).status_code, 200)

    def test_legacy_staff_and_unrelated_role_have_no_analytics(self):
        for user in (self.product, self.legacy):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("dashboard:analytics")).status_code, 403)
            self.assertEqual(self.client.get(reverse("dashboard:analytics_export_products_csv")).status_code, 403)
