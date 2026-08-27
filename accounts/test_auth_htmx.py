"""Focused regression coverage for GET-only auth-method workspace switching."""

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from pathlib import Path


@override_settings(SMS_ENABLED=True, SMS_OTP_LOGIN_ENABLED=True, SMS_BACKEND="accounts.sms.MemorySmsBackend")
class AuthMethodSwitchingTests(TestCase):
    def setUp(self):
        cache.clear()
    def _get(self, url, *, htmx=False):
        return self.client.get(url, HTTP_HX_REQUEST="true") if htmx else self.client.get(url)

    def assert_workspace_fragment(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="auth-workspace"')
        self.assertNotContains(response, "<html")
        self.assertNotContains(response, "hx-boost=")

    def assert_full_workspace_page(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="auth-workspace"')
        self.assertContains(response, "<html")
        self.assertNotContains(response, "hx-boost=")

    def test_login_full_pages_and_hx_fragments_have_explicit_workspace_target(self):
        password_url = reverse("accounts:login")
        sms_url = reverse("accounts:sms_login_request")

        password_page = self._get(password_url)
        sms_page = self._get(sms_url)
        self.assert_full_workspace_page(password_page)
        self.assert_full_workspace_page(sms_page)
        self.assertContains(password_page, f'href="{sms_url}"')
        self.assertContains(sms_page, f'href="{password_url}"')

        password_fragment = self._get(password_url, htmx=True)
        sms_fragment = self._get(sms_url, htmx=True)
        self.assert_workspace_fragment(password_fragment)
        self.assert_workspace_fragment(sms_fragment)
        for response in (password_fragment, sms_fragment):
            self.assertContains(response, 'hx-target="#auth-workspace"')
            self.assertContains(response, 'hx-swap="outerHTML"')
            self.assertContains(response, 'csrfmiddlewaretoken')

    def test_signup_full_pages_and_hx_fragments_have_explicit_workspace_target(self):
        standard_url = reverse("accounts:register")
        mobile_url = reverse("accounts:sms_signup_request")

        standard_page = self._get(standard_url)
        mobile_page = self._get(mobile_url)
        self.assert_full_workspace_page(standard_page)
        self.assert_full_workspace_page(mobile_page)
        self.assertContains(standard_page, f'href="{mobile_url}"')
        self.assertContains(mobile_page, f'href="{standard_url}"')

        standard_fragment = self._get(standard_url, htmx=True)
        mobile_fragment = self._get(mobile_url, htmx=True)
        self.assert_workspace_fragment(standard_fragment)
        self.assert_workspace_fragment(mobile_fragment)
        for response in (standard_fragment, mobile_fragment):
            self.assertContains(response, 'hx-target="#auth-workspace"')
            self.assertContains(response, 'hx-swap="outerHTML"')
            self.assertContains(response, 'csrfmiddlewaretoken')

    def test_fa_and_en_method_labels_are_rendered_in_their_respective_locales(self):
        fa_login = self._get("/fa/accounts/login/")
        fa_register = self._get("/fa/accounts/register/")
        self.assertContains(fa_login, "ورود با گذرواژه")
        self.assertContains(fa_login, "ورود با کد پیامکی")
        self.assertContains(fa_register, "ثبت‌نام عادی")
        self.assertContains(fa_register, "ثبت‌نام با موبایل")

        en_login = self._get("/en/accounts/login/")
        en_register = self._get("/en/accounts/register/")
        self.assertContains(en_login, "Sign in with password")
        self.assertContains(en_login, "Sign in with SMS code")
        self.assertContains(en_register, "Standard signup")
        self.assertContains(en_register, "Sign up with mobile")

    def test_post_auth_forms_remain_full_page_flows(self):
        invalid_login = self.client.post(reverse("accounts:login"), {"username": "", "password": ""})
        invalid_register = self.client.post(reverse("accounts:register"), {"username": ""})
        self.assert_full_workspace_page(invalid_login)
        self.assert_full_workspace_page(invalid_register)

    def test_explicit_canonical_actions_preserve_swapped_method_on_success_and_validation_error(self):
        password = self._get("/en/accounts/login/")
        sms_login = self._get("/en/accounts/login/sms/", htmx=True)
        standard = self._get("/en/accounts/register/")
        sms_signup = self._get("/en/accounts/register/sms/", htmx=True)
        self.assertContains(password, 'action="/en/accounts/login/"')
        self.assertContains(sms_login, 'action="/en/accounts/login/sms/"')
        self.assertContains(standard, 'action="/en/accounts/register/"')
        self.assertContains(sms_signup, 'action="/en/accounts/register/sms/"')
        for response in (password, sms_login, standard, sms_signup):
            self.assertNotContains(response, "hx-post=")

        invalid_login = self.client.post("/en/accounts/login/sms/", {"phone": "not-a-number"})
        invalid_signup = self.client.post("/en/accounts/register/sms/", {"phone": "not-a-number"})
        for response, action in ((invalid_login, "/en/accounts/login/sms/"), (invalid_signup, "/en/accounts/register/sms/")):
            self.assert_full_workspace_page(response)
            self.assertContains(response, f'action="{action}"')
            self.assertContains(response, 'name="phone"')

        login_request = self.client.post("/en/accounts/login/sms/", {"phone": "09121234567"})
        signup_request = self.client.post("/en/accounts/register/sms/", {"phone": "09121234568"})
        self.assertEqual(login_request.status_code, 302)
        self.assertIn("/en/accounts/login/sms/", login_request["Location"])
        self.assertEqual(signup_request.status_code, 302)
        self.assertIn("/en/accounts/register/sms/", signup_request["Location"])

    def test_cross_auth_footer_is_once_per_full_shell_and_absent_from_fragments(self):
        for url, text in (("/en/accounts/login/", "Don't have an account?"), ("/en/accounts/login/sms/", "Don't have an account?"), ("/en/accounts/register/", "Already have an account?"), ("/en/accounts/register/sms/", "Already have an account?")):
            self.assertEqual(self._get(url).content.decode().count(text), 1)
        for url in ("/en/accounts/login/", "/en/accounts/login/sms/", "/en/accounts/register/", "/en/accounts/register/sms/"):
            fragment = self._get(url, htmx=True).content.decode()
            self.assertNotIn("Don't have an account?", fragment)
            self.assertNotIn("Already have an account?", fragment)

    def test_shared_navigation_does_not_intercept_auth_workspace_swaps(self):
        source = (Path(__file__).resolve().parents[1] / "static" / "js" / "htmx-get-navigation.js").read_text(encoding="utf-8")
        self.assertIn('if (targetId === "auth-workspace") return;', source)
