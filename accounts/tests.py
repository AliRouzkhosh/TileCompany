from pathlib import Path

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation
from django.utils.translation import override

from .forms import AddressForm, ProfileUpdateForm, RegisterForm
from .models import Address, Profile
from .selectors import get_account_overview
from orders.models import Order


class LanguageIsolationMixin:
    """Restore the translation context changed by localized client requests."""

    def setUp(self):
        super().setUp()
        self.enterContext(translation.override("en"))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AuthenticationLocalizationTests(LanguageIsolationMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="buyer", email="buyer@example.com", password="secret-pass-123"
        )

    def test_persian_login_registration_and_reset_pages_are_translated(self):
        cases = (
            ("/fa/accounts/login/", "ورود"),
            ("/fa/accounts/register/", "ایجاد حساب کاربری"),
            ("/fa/accounts/password-reset/", "بازنشانی گذرواژه"),
            ("/fa/accounts/password-reset/done/", "ایمیل خود را بررسی کنید"),
            ("/fa/accounts/reset/invalid/invalid/", "پیوند بازنشانی گذرواژه منقضی شده است"),
            ("/fa/accounts/reset/done/", "گذرواژه به‌روزرسانی شد"),
        )
        for url, expected in cases:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, expected)

    def test_english_authentication_pages_remain_english(self):
        response = self.client.get("/en/accounts/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome back")
        self.assertContains(response, "Login")
        self.assertContains(response, "Forgot password?")

    def test_persian_reset_email_uses_translated_subject_and_body(self):
        response = self.client.post(
            "/fa/accounts/password-reset/", {"email": self.user.email}
        )
        self.assertRedirects(response, "/fa/accounts/password-reset/done/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "بازنشانی گذرواژهٔ تایل‌استور")
        self.assertIn("درخواستی برای بازنشانی گذرواژه", mail.outbox[0].body)
        self.assertIn("/fa/accounts/reset/", mail.outbox[0].body)

    def test_authentication_form_labels_translate_without_changing_fields(self):
        with override("fa"):
            form = RegisterForm()
            self.assertEqual(str(form.fields["username"].label), "نام کاربری")
            self.assertEqual(str(form.fields["password1"].label), "گذرواژه")
            self.assertEqual(str(form.fields["email"].label), "نشانی ایمیل")

    def test_login_invalid_successful_and_next_redirects_are_preserved(self):
        login_url = reverse("accounts:login")
        invalid = self.client.post(login_url, {"username": self.user.username, "password": "wrong"})
        self.assertContains(invalid, "Please enter a correct username and password")

        target = reverse("accounts:profile")
        response = self.client.post(f"{login_url}?next={target}", {"username": self.user.username, "password": "secret-pass-123"})
        self.assertRedirects(response, target)

    def test_register_validation_duplicate_and_login_destination(self):
        register_url = reverse("accounts:register")
        invalid = self.client.post(register_url, {"username": "", "email": "bad", "password1": "x", "password2": "y"})
        self.assertEqual(invalid.status_code, 200)
        self.assertTrue(invalid.context["form"].errors)

        duplicate = self.client.post(register_url, {"username": self.user.username, "email": "new@example.com", "password1": "valid-password-123", "password2": "valid-password-123"})
        self.assertIn("username", duplicate.context["form"].errors)

        response = self.client.get(register_url)
        self.assertContains(response, reverse("accounts:login"))
        self.assertContains(response, "sf-auth-control")
        shell_source = (Path(__file__).resolve().parent / "templates" / "accounts" / "components" / "auth_container_start.html").read_text(encoding="utf-8")
        self.assertNotIn("store-container", shell_source)

    def test_reset_request_complete_and_invalid_token_routes_render(self):
        response = self.client.post(reverse("accounts:password_reset"), {"email": self.user.email})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.client.get(reverse("accounts:password_reset_complete")).status_code, 200)
        self.assertEqual(self.client.get("/en/accounts/reset/invalid/invalid/").status_code, 200)

class AccountCenterLocalizationTests(LanguageIsolationMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="buyer-ltr", email="buyer@example.com", password="secret-pass-123",
            first_name="Ali",
        )
        self.profile = Profile.objects.create(user=self.user, phone="+989121234567")
        self.client.force_login(self.user)

    def test_persian_account_overview_and_profile_are_localized(self):
        overview = self.client.get("/fa/accounts/")
        self.assertEqual(overview.status_code, 200)
        self.assertContains(overview, "حساب کاربری من")
        self.assertContains(overview, '<p class="text-sm font-semibold text-brand">حساب کاربری</p>')
        self.assertNotContains(overview, '<p class="text-sm font-semibold text-brand">Account</p>')
        self.assertNotContains(overview, "مجموع سفارش‌ها")
        self.assertContains(overview, "Ali")
        for persian, english in (
            ("ویرایش پروفایل", "Edit profile"),
            ("سفارش‌های من", "My orders"),
            ("مشاهدهٔ همهٔ سفارش‌ها", "View all orders"),
            ("جاری", "Current"),
            ("نشانی پیش‌فرض ارسال", "Default shipping address"),
            ("افزودن نشانی", "Add address"),
        ):
            self.assertContains(overview, persian)
            self.assertNotContains(overview, english)

        profile = self.client.get("/fa/accounts/profile/")
        self.assertEqual(profile.status_code, 200)
        self.assertContains(profile, "اطلاعات شخصی")
        self.assertContains(profile, "ذخیرهٔ تغییرات")
        self.assertContains(profile, "نام خانوادگی")

    def test_profile_form_labels_and_success_message_localize_and_persist(self):
        with override("fa"):
            form = ProfileUpdateForm(instance=self.profile)
            self.assertEqual(str(form.fields["phone"].label), "شماره تلفن")
            self.assertEqual(str(form.fields["company_name"].label), "نام شرکت")

        response = self.client.post(
            "/fa/accounts/profile/",
            {
                "first_name": "Ali", "last_name": "Karimi", "email": "buyer@example.com",
                "phone": "+989121234567", "birthdate": "", "gender": "male",
                "company_name": "Example Co.", "company_tax_id": "TAX-123",
            },
            follow=True,
        )
        self.assertRedirects(response, "/fa/accounts/profile/")
        self.assertContains(response, "پروفایل شما با موفقیت به‌روزرسانی شد.")
        self.profile.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.user.last_name, "Karimi")
        self.assertEqual(self.profile.company_name, "Example Co.")

    def test_english_account_center_remains_english(self):
        response = self.client.get("/en/accounts/profile/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Personal information")
        self.assertContains(response, "Save changes")

    def test_overview_uses_one_status_aggregation_without_recent_order_rows(self):
        for status in (
            Order.Status.PENDING,
            Order.Status.CONFIRMED,
            Order.Status.DELIVERED,
            Order.Status.CANCELLED,
        ):
            Order.objects.create(
                user=self.user,
                full_name="Buyer",
                address="1 Example Street",
                city="Tehran",
                phone="123",
                status=status,
            )

        with self.assertNumQueries(3):
            overview = get_account_overview(self.user)
        self.assertNotIn("recent_orders", overview)
        self.assertEqual(
            overview["order_status_counts"],
            {"current": 1, "delivered": 1, "cancelled": 1},
        )

    def test_overview_hub_renders_status_summary_not_order_rows(self):
        Order.objects.create(user=self.user, full_name="Buyer", address="1", city="Tehran", phone="123", status=Order.Status.CONFIRMED)
        Order.objects.create(user=self.user, full_name="Buyer", address="1", city="Tehran", phone="123", status=Order.Status.PENDING)
        response = self.client.get("/en/accounts/")
        self.assertContains(response, "My orders")
        self.assertContains(response, "View all orders")
        self.assertContains(response, "Current")
        self.assertContains(response, "Delivered")
        self.assertContains(response, "Cancelled")
        self.assertContains(response, "data-orders-summary")
        self.assertContains(response, "account-overview__top-grid")
        self.assertContains(response, "account-orders-panel__cells")
        self.assertContains(response, 'data-lucide="package-check"')
        self.assertNotContains(response, "Recent orders")
        self.assertNotContains(response, "#1")

    def test_overview_uses_one_orderable_grid_for_mobile_and_desktop_composition(self):
        response = self.client.get("/en/accounts/")
        content = response.content.decode()
        identity_position = content.index("account-identity-card")
        orders_position = content.index("data-orders-summary")
        address_position = content.index("account-address-card")

        self.assertLess(identity_position, orders_position)
        self.assertLess(orders_position, address_position)
        self.assertEqual(content.count("data-orders-summary"), 1)
        self.assertEqual(content.count("account-address-card"), 1)

    def test_overview_hub_uses_default_address_summary_when_available(self):
        Address.objects.create(
            user=self.user, first_name="Ali", last_name="Karimi", phone="123",
            country="Iran", province="Tehran", city="Tehran", street_address="1 Example Street",
            is_default_shipping=True,
        )
        response = self.client.get("/en/accounts/")
        self.assertContains(response, "Default shipping address")
        self.assertContains(response, "Manage addresses")
        self.assertContains(response, "account-address-card")
        self.assertNotContains(response, "Add a saved address to make checkout faster.")

    def test_overview_identity_uses_existing_optional_profile_details_only(self):
        self.profile.phone = "+989121234567"
        self.profile.is_business = True
        self.profile.company_name = "Example Co."
        self.profile.save(update_fields=["phone", "is_business", "company_name"])
        response = self.client.get("/en/accounts/")
        self.assertContains(response, "account-identity-card__avatar")
        self.assertContains(response, "+989121234567")
        self.assertContains(response, "Example Co.")
        self.assertContains(response, reverse("accounts:profile"))
        self.assertContains(response, 'data-lucide="building-2"')


class AddressLocalizationTests(LanguageIsolationMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="address-buyer", email="address@example.com", password="secret-pass-123"
        )
        self.client.force_login(self.user)

    def address_data(self, **overrides):
        data = {
            "label": "home", "first_name": "Ali", "last_name": "Karimi",
            "company_name": "Example Co.", "phone": "+989121234567",
            "country": "Iran", "province": "Tehran", "city": "Tehran",
            "street_address": "12 Example Street", "postal_code": "1234567890",
        }
        data.update(overrides)
        return data

    def test_persian_address_pages_and_form_labels_are_localized(self):
        response = self.client.get("/fa/accounts/addresses/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "هنوز نشانی‌ای ثبت نشده است")

        create = self.client.get("/fa/accounts/addresses/add/")
        self.assertEqual(create.status_code, 200)
        self.assertContains(create, "افزودن نشانی")
        with override("fa"):
            form = AddressForm()
            self.assertEqual(str(form.fields["postal_code"].label), "کد پستی")
            self.assertEqual(str(form.fields["is_default_shipping"].label), "استفاده به‌عنوان نشانی پیش‌فرض ارسال")

    def test_address_crud_default_behavior_and_localized_messages(self):
        old_address = Address.objects.create(
            user=self.user, label="office", first_name="Old", last_name="Address",
            phone="100", country="Iran", province="Tehran", city="Tehran",
            street_address="Old street", is_default_shipping=True,
        )
        response = self.client.post(
            "/fa/accounts/addresses/add/",
            self.address_data(is_default_shipping="on", is_default_billing="on"),
            follow=True,
        )
        self.assertRedirects(response, "/fa/accounts/addresses/")
        self.assertContains(response, "نشانی با موفقیت افزوده شد.")
        address = Address.objects.get(user=self.user, first_name="Ali")
        old_address.refresh_from_db()
        self.assertTrue(address.is_default_shipping)
        self.assertTrue(address.is_default_billing)
        self.assertFalse(old_address.is_default_shipping)

        response = self.client.post(
            f"/fa/accounts/addresses/{address.pk}/edit/",
            self.address_data(city="Shiraz", is_default_shipping="on"),
            follow=True,
        )
        self.assertRedirects(response, "/fa/accounts/addresses/")
        self.assertContains(response, "نشانی با موفقیت به‌روزرسانی شد.")
        address.refresh_from_db()
        self.assertEqual(address.city, "Shiraz")

        confirmation = self.client.get(f"/fa/accounts/addresses/{address.pk}/delete/")
        self.assertContains(confirmation, "آیا از حذف این نشانی مطمئن هستید؟")
        response = self.client.post(f"/fa/accounts/addresses/{address.pk}/delete/", follow=True)
        self.assertRedirects(response, "/fa/accounts/addresses/")
        self.assertContains(response, "نشانی حذف شد.")
        self.assertFalse(Address.objects.filter(pk=address.pk).exists())

    def test_address_ownership_and_english_regression(self):
        other = get_user_model().objects.create_user(username="other-address", password="secret-pass-123")
        address = Address.objects.create(
            user=other, first_name="Other", last_name="User", phone="100",
            country="Iran", province="Tehran", city="Tehran", street_address="Other street",
        )
        self.assertEqual(self.client.get(f"/en/accounts/addresses/{address.pk}/edit/").status_code, 404)
        response = self.client.get("/en/accounts/addresses/add/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add address")
        self.assertContains(response, "Save address")


class AccountShellTests(LanguageIsolationMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="account-shell", password="secret-pass-123", first_name="Customer"
        )
        Profile.objects.create(user=self.user)
        self.client.force_login(self.user)

    def test_representative_account_pages_share_one_navigation_and_active_destination(self):
        cases = (
            ("accounts:overview", "Overview"),
            ("accounts:profile", "Profile"),
            ("accounts:address_list", "Addresses"),
        )
        for route, active_label in cases:
            with self.subTest(route=route):
                with override("en"):
                    response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "data-account-navigation", count=1)
                self.assertContains(response, 'class="account-shell"')
                self.assertContains(response, 'class="store-container flex-1 py-6 sm:py-8"')
                self.assertContains(response, "account-shell__layout mt-4 grid min-w-0 gap-5 lg:grid-cols-12")
                self.assertContains(response, 'aria-current="page"')
                self.assertContains(response, active_label)
                for label in ("Orders", "Wishlist", "Notifications", "Support"):
                    self.assertContains(response, label)

    def test_account_shell_is_localized_and_preserves_global_payment_attention(self):
        Order.objects.create(
            user=self.user, full_name="Customer", address="1", city="Tehran", phone="123",
            payment_method=Order.PaymentMethod.MOCK, currency="USD",
        )
        with override("fa"):
            response = self.client.get("/fa/accounts/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-account-navigation", count=1)
        self.assertContains(response, "data-payment-attention")

    def test_account_nav_uses_a_mobile_current_section_selector_without_swipe_code(self):
        with override("en"):
            response = self.client.get(reverse("accounts:profile"))
        self.assertContains(response, "data-account-nav-selector")
        self.assertContains(response, "accountNavSelectorMenu")
        self.assertContains(response, "Profile")
        self.assertContains(response, 'aria-current="page"')
        script = (Path(__file__).resolve().parents[1] / "static" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertNotContains(response, "data-account-nav-auto-reveal")
        self.assertNotIn("scrollIntoView", script)
        self.assertNotIn("data-account-nav-more", script)
        self.assertIn("data-account-nav-selector", script)
        self.assertIn('event.key !== "Escape"', script)
        self.assertIn("restoreFocus", script)

    def test_mobile_account_selector_reuses_canonical_items_and_notification_context(self):
        response = self.client.get(reverse("accounts:profile"))
        source = (Path(__file__).resolve().parent / "templates" / "accounts" / "components" / "sidebar.html").read_text(encoding="utf-8")
        items_source = (Path(__file__).resolve().parent / "templatetags" / "account_navigation.py").read_text(encoding="utf-8")
        script = (Path(__file__).resolve().parents[1] / "static" / "js" / "main.js").read_text(encoding="utf-8")

        self.assertContains(response, "data-account-nav-selector")
        self.assertContains(response, "accountNavSelectorMenu")
        self.assertContains(response, 'aria-controls="accountNavSelectorMenu"')
        self.assertContains(response, 'role="menu"')
        self.assertIn("hidden data-account-nav-selector-menu", source)
        self.assertIn("lg:hidden", source)
        self.assertIn("account_navigation_items request", source)
        for route in ("accounts:overview", "orders:order_list", "products:wishlist", "notifications:center", "support:ticket_list"):
            self.assertIn(route, items_source)
        self.assertIn("notification_unread_count", (Path(__file__).resolve().parent / "templates" / "accounts" / "components" / "account_nav_links.html").read_text(encoding="utf-8"))
        self.assertIn("data-account-nav-selector", script)
        self.assertIn('event.key !== "Escape"', script)
        self.assertIn("restoreFocus", script)

    def test_mobile_selector_has_one_shared_structure_and_icon_authority_after_htmx_navigation(self):
        sidebar = (Path(__file__).resolve().parent / "templates" / "accounts" / "components" / "sidebar.html").read_text(encoding="utf-8")
        navigation = (Path(__file__).resolve().parent / "templatetags" / "account_navigation.py").read_text(encoding="utf-8")
        htmx_script = (Path(__file__).resolve().parents[1] / "static" / "js" / "htmx-get-navigation.js").read_text(encoding="utf-8")

        normal = self.client.get(reverse("accounts:profile"))
        htmx = self.client.get(reverse("accounts:profile"), HTTP_HX_REQUEST="true", HTTP_HX_TARGET="account-workspace")
        for response in (normal, htmx):
            self.assertContains(response, '<div class="relative lg:hidden" data-account-nav-selector>', count=1)
            self.assertContains(response, "data-account-nav-selector-button", count=1)
            self.assertContains(response, "data-account-nav-selector-menu", count=1)
            self.assertContains(response, 'data-lucide="user-round"')
            self.assertContains(response, "Profile")

        self.assertIn("account_navigation_current account_navigation_items", sidebar)
        self.assertIn('data-lucide="chevron-down"', sidebar)
        for icon in ("layout-dashboard", "package", "heart", "map-pin", "user-round", "bell", "headset"):
            self.assertIn(f'"{icon}"', navigation)
        self.assertIn("window.lucide?.createIcons()", htmx_script)

    def test_account_shell_retains_login_protection(self):
        self.client.logout()
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_htmx_get_navigation_disables_automatic_boost_scrolling(self):
        nav = (Path(__file__).resolve().parent / "templates" / "accounts" / "components" / "account_nav_links.html").read_text(encoding="utf-8")
        base = (Path(__file__).resolve().parents[1] / "templates" / "base.html").read_text(encoding="utf-8")
        htmx_config = (Path(__file__).resolve().parents[1] / "static" / "js" / "htmx-config.js").read_text(encoding="utf-8")
        script = (Path(__file__).resolve().parents[1] / "static" / "js" / "htmx-get-navigation.js").read_text(encoding="utf-8")
        workspace = (Path(__file__).resolve().parent / "templates" / "accounts" / "components" / "account_workspace.html").read_text(encoding="utf-8")
        workspace_paths = (
            Path(__file__).resolve().parents[1] / "blog" / "templates" / "blog" / "components" / "article_discovery.html",
            Path(__file__).resolve().parents[1] / "support" / "templates" / "support" / "components" / "help_discovery.html",
            Path(__file__).resolve().parents[1] / "support" / "templates" / "support" / "components" / "ticket_list_workspace.html",
            Path(__file__).resolve().parents[1] / "notifications" / "templates" / "notifications" / "components" / "notification_workspace.html",
        )
        self.assertIn('hx-swap="outerHTML show:none"', nav)
        self.assertIn('data-htmx-intent="navigation"', nav)
        self.assertIn("{% static 'js/htmx-config.js' %}", base)
        self.assertLess(
            base.index("vendor/node_modules/htmx.org/dist/htmx.min.js"),
            base.index("{% static 'js/htmx-config.js' %}"),
        )
        self.assertIn("htmx.config.scrollIntoViewOnBoost = false", htmx_config)
        self.assertIn("previousScrollY", script)
        self.assertIn("maxScrollY", script)
        self.assertIn("scrollStability", script)
        self.assertIn("HX-History-Restore-Request", script)
        self.assertNotIn("data-htmx-results-start", script)
        self.assertNotIn("show:bottom", script)
        self.assertIn('class="scroll-mt-24"', workspace)
        for workspace_path in workspace_paths:
            self.assertNotIn("data-htmx-results-start", workspace_path.read_text(encoding="utf-8"))
        self.assertNotIn("show:bottom", nav)

    def test_pagination_scroll_contract_is_delegated_and_preserves_history_restores(self):
        script = (Path(__file__).resolve().parents[1] / "static" / "js" / "htmx-get-navigation.js").read_text(encoding="utf-8")

        self.assertIn('closest?.("[data-pagination-link]")', script)
        self.assertIn("data-pagination-scroll-target", script)
        self.assertIn("state.pagination", script)
        self.assertIn("HX-History-Restore-Request", script)

    def test_logout_uses_one_shared_post_confirmation_form(self):
        response = self.client.get(reverse("accounts:overview"))
        self.assertContains(response, "data-logout-dialog")
        self.assertContains(response, "data-logout-trigger", count=2)
        self.assertContains(response, 'method="post" action="/en/accounts/logout/"', count=1)
        self.assertContains(response, "data-logout-form")
        self.assertIn("csrfmiddlewaretoken", response.content.decode())
        self.assertContains(response, "data-logout-cancel")

    def test_logout_post_ends_session_and_get_does_not_mutate(self):
        logout_url = reverse("accounts:logout")
        get_response = self.client.get(logout_url)
        self.assertEqual(get_response.status_code, 405)
        self.assertTrue("_auth_user_id" in self.client.session)

        response = self.client.post(logout_url)
        self.assertRedirects(response, reverse("products:home"))
        self.assertNotIn("_auth_user_id", self.client.session)
