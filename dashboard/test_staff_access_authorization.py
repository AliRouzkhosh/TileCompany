from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from dashboard.models import StaffAccessAuditEvent
from dashboard.permissions import user_can_access_dashboard
from dashboard.management.commands.seed_dashboard_roles import Command as SeedDashboardRolesCommand


class StaffAccessAuthorizationTests(TestCase):
    def setUp(self):
        self.language = translation.override("en")
        self.language.__enter__()
        SeedDashboardRolesCommand().handle()
        User = get_user_model()
        self.owner = User.objects.create_superuser("owner", "owner@example.test", "secret")
        self.target = User.objects.create_user("target", "target@example.test", "secret")
        self.product = User.objects.create_user("product", password="secret")
        self.product.groups.add(Group.objects.get(name="Product Manager"))
        self.other_owner = User.objects.create_superuser("other-owner", "other@example.test", "secret")

    def tearDown(self):
        self.language.__exit__(None, None, None)

    def update_url(self, user=None):
        return reverse("dashboard:staff_access:update_roles", args=[(user or self.target).pk])

    def test_owner_assigns_and_revokes_roles_without_admin_staff_flag(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.update_url(), {"roles": ["Product Manager"]}).status_code, 302)
        self.target.refresh_from_db()
        self.assertTrue(user_can_access_dashboard(self.target))
        self.assertFalse(self.target.is_staff)
        event = StaffAccessAuditEvent.objects.get()
        self.assertEqual(event.roles_before, [])
        self.assertEqual(event.roles_after, ["Product Manager"])
        self.client.post(self.update_url(), {})
        self.target = get_user_model().objects.get(pk=self.target.pk)
        self.assertFalse(user_can_access_dashboard(self.target))
        self.assertEqual(StaffAccessAuditEvent.objects.latest("pk").action, "dashboard_access_revoked")

    def test_non_owner_unknown_role_and_superuser_target_are_safe(self):
        self.client.force_login(self.product)
        self.assertEqual(self.client.post(self.update_url(self.product), {"roles": ["Order Manager"]}).status_code, 403)
        self.product.refresh_from_db()
        self.assertTrue(self.product.groups.filter(name="Product Manager").exists())
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.update_url(), {"roles": ["Product Manager", "Administrator"]}).status_code, 302)
        self.assertFalse(self.target.groups.filter(name="Product Manager").exists())
        self.assertEqual(self.client.post(self.update_url(self.other_owner), {"roles": ["Analyst"]}).status_code, 302)
        self.assertEqual(StaffAccessAuditEvent.objects.count(), 0)

    def test_unrelated_groups_and_direct_permissions_are_preserved_and_warned(self):
        external = Group.objects.create(name="External Reporting")
        self.target.groups.add(external, Group.objects.get(name="Product Manager"))
        self.target.user_permissions.add(Permission.objects.get(content_type__app_label="products", codename="change_inventory"))
        self.client.force_login(self.owner)
        detail = self.client.get(reverse("dashboard:staff_access:detail", args=[self.target.pk]))
        self.assertContains(detail, "directly assigned permissions")
        self.client.post(self.update_url(), {"roles": ["Content Manager"]})
        self.target.refresh_from_db()
        self.assertTrue(self.target.groups.filter(name="External Reporting").exists())
        self.assertTrue(self.target.groups.filter(name="Content Manager").exists())
        self.assertTrue(self.target.user_permissions.filter(codename="change_inventory").exists())

    def test_staff_access_uses_localized_role_labels_without_renaming_groups(self):
        self.client.force_login(self.owner)
        with translation.override("fa"):
            listing = self.client.get(reverse("dashboard:staff_access:list"))
            detail = self.client.get(
                reverse("dashboard:staff_access:detail", args=[self.product.pk])
            )

        self.assertContains(listing, "مدیریت کارکنان و دسترسی‌ها")
        self.assertContains(listing, "مالک / مدیر ارشد")
        self.assertContains(detail, "مدیر محصولات")
        self.assertContains(detail, "محصولات، گونه‌ها، موجودی، قیمت‌ها و عملیات کاتالوگ")
        self.assertNotContains(detail, ">Product Manager<")
        self.assertTrue(self.product.groups.filter(name="Product Manager").exists())
