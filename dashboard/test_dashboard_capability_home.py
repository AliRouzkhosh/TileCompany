from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from dashboard.management.commands.seed_dashboard_roles import Command as SeedDashboardRolesCommand


class DashboardCapabilityHomeTests(TestCase):
    def setUp(self):
        SeedDashboardRolesCommand().handle()
        User = get_user_model()
        self.users = {}
        for group_name in ("Product Manager", "Order Manager", "Support Manager", "Content Manager", "Moderator", "Analyst"):
            user = User.objects.create_user(group_name.lower().replace(" ", "-"), password="secret")
            user.groups.add(Group.objects.get(name=group_name))
            self.users[group_name] = user
        self.owner = User.objects.create_superuser("owner", "owner@example.test", "secret")

    def home(self, user):
        self.client.force_login(user)
        with translation.override("en"):
            return self.client.get(reverse("dashboard:home"))

    def test_each_role_sees_only_its_home_capability(self):
        self.assertContains(self.home(self.users["Product Manager"]), "Inventory attention")
        self.assertNotContains(self.home(self.users["Product Manager"]), "Order operations")
        self.assertContains(self.home(self.users["Order Manager"]), "Order operations")
        self.assertNotContains(self.home(self.users["Order Manager"]), "Refund operations")
        self.assertContains(self.home(self.users["Support Manager"]), "Open tickets")
        self.assertContains(self.home(self.users["Content Manager"]), "Editorial")
        self.assertContains(self.home(self.users["Moderator"]), "Only your authorized moderation queues")
        self.assertContains(self.home(self.users["Analyst"]), "Read-only reporting")

    def test_multi_role_home_is_the_permission_union_and_owner_sees_refunds(self):
        user = self.users["Product Manager"]
        user.groups.add(Group.objects.get(name="Content Manager"))
        response = self.home(user)
        self.assertContains(response, "Inventory attention")
        self.assertContains(response, "Editorial")
        self.assertNotContains(response, "Refund operations")
        self.assertContains(self.home(self.owner), "Refund operations")
