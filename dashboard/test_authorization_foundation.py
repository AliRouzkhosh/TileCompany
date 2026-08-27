from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import translation

from dashboard.permissions import (
    dashboard_permission_required,
    user_can_access_dashboard,
)
from dashboard.role_definitions import ROLE_DEFINITIONS


class AuthorizationFoundationTests(TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(translation.override("en"))
        call_command("seed_dashboard_roles", verbosity=0)
        self.User = get_user_model()
        self.factory = RequestFactory()
        self.customer = self.User.objects.create_user("customer", password="secret")
        self.product_manager = self.User.objects.create_user("product-manager", password="secret")
        self.product_manager.groups.add(Group.objects.get(name="Product Manager"))
        self.owner = self.User.objects.create_superuser("owner", "owner@example.com", "secret")

    def test_dashboard_eligibility_uses_known_permissions_not_is_staff(self):
        staff_without_capability = self.User.objects.create_user("admin-staff", password="secret", is_staff=True)
        unrelated = self.User.objects.create_user("unrelated", password="secret")
        unrelated.user_permissions.add(Permission.objects.get(content_type__app_label="auth", codename="add_group"))

        self.assertFalse(user_can_access_dashboard(self.customer))
        self.assertFalse(user_can_access_dashboard(staff_without_capability))
        self.assertTrue(user_can_access_dashboard(self.product_manager))
        self.assertTrue(user_can_access_dashboard(self.owner))
        self.assertFalse(user_can_access_dashboard(unrelated))

    def test_group_based_user_can_open_permission_aware_dashboard_but_not_admin(self):
        self.assertFalse(self.product_manager.is_staff)
        self.client.force_login(self.product_manager)
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your authorized operational overview")
        self.assertNotContains(response, "Fulfillment")

        admin_response = self.client.get(reverse("admin:index"))
        self.assertEqual(admin_response.status_code, 302)
        self.assertIn(reverse("admin:login"), admin_response.url)

        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_account_navigation_uses_dashboard_capability(self):
        self.client.force_login(self.customer)
        self.assertNotContains(self.client.get(reverse("products:home")), "Admin Dashboard")

        self.client.force_login(self.product_manager)
        self.assertContains(self.client.get(reverse("products:home")), "Admin Dashboard")

    def test_owner_routes_redirect_anonymous_and_403_authenticated_non_owner(self):
        schema_url = reverse("dashboard:schema:product_type_create")
        anonymous = self.client.post(schema_url, {})
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("accounts:login"), anonymous.url)

        self.client.force_login(self.product_manager)
        denied = self.client.post(schema_url, {})
        self.assertEqual(denied.status_code, 403)
        self.assertContains(denied, "You don't have permission to access this page", status_code=403)

        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(schema_url).status_code, 200)

    def test_permission_decorator_all_any_anonymous_denial_and_superuser(self):
        product_permission = "products.change_inventory"
        order_permission = "orders.manage_fulfillment"

        @dashboard_permission_required(product_permission, order_permission)
        def all_view(request):
            from django.http import HttpResponse
            return HttpResponse("allowed")

        @dashboard_permission_required(product_permission, order_permission, any_of=True)
        def any_view(request):
            from django.http import HttpResponse
            return HttpResponse("allowed")

        anonymous_request = self.factory.get("/protected/")
        from django.contrib.auth.models import AnonymousUser
        anonymous_request.user = AnonymousUser()
        self.assertEqual(all_view(anonymous_request).status_code, 302)

        denied_request = self.factory.get("/protected/")
        denied_request.user = self.customer
        with self.assertRaises(PermissionDenied):
            all_view(denied_request)

        self.product_manager.refresh_from_db()
        all_denied = self.factory.get("/protected/")
        all_denied.user = self.product_manager
        with self.assertRaises(PermissionDenied):
            all_view(all_denied)
        any_allowed = self.factory.get("/protected/")
        any_allowed.user = self.product_manager
        self.assertEqual(any_view(any_allowed).status_code, 200)

        self.owner.refresh_from_db()
        owner_request = self.factory.get("/protected/")
        owner_request.user = self.owner
        self.assertEqual(all_view(owner_request).status_code, 200)

    def test_role_command_is_idempotent_and_converges_permissions(self):
        initial_users = self.User.objects.count()
        product_group = Group.objects.get(name="Product Manager")
        extra = Permission.objects.get(content_type__app_label="auth", codename="add_group")
        product_group.permissions.add(extra)

        call_command("seed_dashboard_roles", verbosity=0)
        call_command("seed_dashboard_roles", verbosity=0)

        self.assertEqual(Group.objects.filter(name__in=ROLE_DEFINITIONS).count(), 6)
        self.assertEqual(self.User.objects.count(), initial_users)
        product_group.refresh_from_db()
        actual = {
            f"{permission.content_type.app_label}.{permission.codename}"
            for permission in product_group.permissions.select_related("content_type")
        }
        self.assertEqual(actual, ROLE_DEFINITIONS["Product Manager"])
        self.assertFalse(self.product_manager.is_staff)
        self.assertFalse(self.product_manager.is_superuser)

    def test_styled_403_is_localized(self):
        self.client.force_login(self.customer)
        with translation.override("fa"):
            response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "شما اجازه دسترسی به این صفحه را ندارید", status_code=403)
