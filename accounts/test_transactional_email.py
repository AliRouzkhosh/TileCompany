from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class WelcomeEmailTests(TestCase):
    def setUp(self):
        self.enterContext(translation.override("en"))

    def registration_payload(self, username="new-buyer", email="buyer@example.com"):
        return {
            "username": username,
            "email": email,
            "password1": "valid-password-123",
            "password2": "valid-password-123",
            "birthdate": "",
            "gender": "",
        }

    def test_successful_registration_sends_localized_welcome_after_commit(self):
        with translation.override("fa"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post("/fa/accounts/register/", self.registration_payload())
        self.assertRedirects(response, "/fa/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["buyer@example.com"])
        self.assertIn("به تایل‌استور خوش آمدید", mail.outbox[0].subject)
        self.assertIn("/fa/accounts/", mail.outbox[0].body)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        self.assertIn('dir="rtl"', mail.outbox[0].alternatives[0].content)

    def test_welcome_delivery_failure_does_not_break_registration(self):
        with patch("accounts.email_delivery.send_transactional_email", side_effect=OSError("smtp down")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse("accounts:register"), self.registration_payload())
        self.assertRedirects(response, reverse("products:home"))
        user = get_user_model().objects.get(username="new-buyer")
        self.assertTrue(self.client.session.get("_auth_user_id"))
        self.assertTrue(hasattr(user, "profile"))

    def test_profile_failure_rolls_back_user_and_schedules_no_welcome(self):
        with patch("accounts.forms.Profile.objects.update_or_create", side_effect=RuntimeError("profile failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("accounts:register"), self.registration_payload())
        self.assertFalse(get_user_model().objects.filter(username="new-buyer").exists())
        self.assertEqual(len(mail.outbox), 0)
