from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .throttling import (
    PASSWORD_LOGIN_IP_LIMIT,
    PASSWORD_LOGIN_IDENTIFIER_IP_LIMIT,
    PASSWORD_RESET_IDENTIFIER_IP_LIMIT,
    SIGNUP_IP_LIMIT,
    password_login_allowed,
)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    TRUST_PROXY_CLIENT_IP=False,
)
class PublicAuthenticationAbuseTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            "customer", "customer@example.test", "correct-password"
        )
        self.login_url = reverse("accounts:login")
        self.reset_url = reverse("accounts:password_reset")
        self.register_url = reverse("accounts:register")

    def tearDown(self):
        cache.clear()

    def login(self, username, password, **meta):
        return self.client.post(self.login_url, {"username": username, "password": password}, **meta)

    def test_valid_login_rotates_session_and_invalid_paths_remain_generic(self):
        session = self.client.session
        session["before_login"] = "value"
        session.save()
        old_key = self.client.cookies["sessionid"].value

        existing = self.login("customer", "wrong-password", REMOTE_ADDR="203.0.113.10")
        unknown = self.login("unknown", "wrong-password", REMOTE_ADDR="203.0.113.11")
        self.assertEqual(existing.status_code, unknown.status_code)
        self.assertContains(existing, "Please enter a correct username and password.")
        self.assertContains(unknown, "Please enter a correct username and password.")

        response = self.login("customer", "correct-password", REMOTE_ADDR="203.0.113.12")
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(self.client.cookies["sessionid"].value, old_key)

    def test_login_layered_limits_and_spoofed_forwarded_ip(self):
        for _ in range(PASSWORD_LOGIN_IDENTIFIER_IP_LIMIT):
            self.login("customer", "wrong-password", REMOTE_ADDR="203.0.113.20")
        throttled = self.login("customer", "wrong-password", REMOTE_ADDR="203.0.113.20")
        self.assertEqual(throttled.status_code, 429)
        self.assertContains(throttled, "Please try again later.", status_code=429)

        cache.clear()
        for attempt in range(PASSWORD_LOGIN_IDENTIFIER_IP_LIMIT):
            self.login(
                "customer",
                "wrong-password",
                REMOTE_ADDR="203.0.113.21",
                HTTP_X_REAL_IP=f"198.51.100.{attempt}",
            )
        self.assertEqual(
            self.login("customer", "wrong-password", REMOTE_ADDR="203.0.113.21", HTTP_X_REAL_IP="198.51.100.99").status_code,
            429,
        )

    def test_success_does_not_clear_broad_ip_budget(self):
        client_ip = "203.0.113.22"
        for attempt in range(PASSWORD_LOGIN_IP_LIMIT - 1):
            self.assertTrue(password_login_allowed(client_ip=client_ip, identifier=f"attempt-{attempt}"))
        self.assertEqual(self.login("customer", "correct-password", REMOTE_ADDR=client_ip).status_code, 302)
        self.assertFalse(password_login_allowed(client_ip=client_ip, identifier="new-identifier"))

    def test_password_reset_is_opaque_and_suppresses_excess_email(self):
        existing = self.client.post(self.reset_url, {"email": "customer@example.test"})
        unknown = self.client.post(self.reset_url, {"email": "unknown@example.test"})
        self.assertEqual(existing.status_code, unknown.status_code)
        self.assertEqual(existing.url, unknown.url)

        cache.clear()
        mail.outbox.clear()
        for _ in range(PASSWORD_RESET_IDENTIFIER_IP_LIMIT):
            self.client.post(self.reset_url, {"email": "customer@example.test"}, REMOTE_ADDR="203.0.113.23")
        suppressed = self.client.post(self.reset_url, {"email": "customer@example.test"}, REMOTE_ADDR="203.0.113.23")
        self.assertEqual(suppressed.status_code, 302)
        self.assertEqual(len(mail.outbox), PASSWORD_RESET_IDENTIFIER_IP_LIMIT)

    def test_signup_ip_limit_does_not_globally_block_other_clients(self):
        for _ in range(SIGNUP_IP_LIMIT):
            response = self.client.post(self.register_url, {}, REMOTE_ADDR="203.0.113.24")
            self.assertNotEqual(response.status_code, 429)
        self.assertEqual(self.client.post(self.register_url, {}, REMOTE_ADDR="203.0.113.24").status_code, 429)
        self.assertNotEqual(self.client.post(self.register_url, {}, REMOTE_ADDR="203.0.113.25").status_code, 429)

    def test_cache_outage_returns_controlled_temporary_failure(self):
        with patch("accounts.throttling.cache.add", side_effect=OSError):
            self.assertEqual(self.login("customer", "wrong-password", REMOTE_ADDR="203.0.113.26").status_code, 503)
        with patch("accounts.throttling.cache.add", side_effect=OSError):
            self.assertEqual(self.client.post(self.reset_url, {"email": "customer@example.test"}, REMOTE_ADDR="203.0.113.27").status_code, 503)
