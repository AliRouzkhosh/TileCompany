from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
import re

from .models import OtpChallenge, PhoneIdentity, Profile
from .sms import outbox


OTP_SETTINGS = {"SMS_ENABLED": True, "SMS_OTP_LOGIN_ENABLED": True, "SMS_BACKEND": "accounts.sms.MemorySmsBackend"}


@override_settings(**OTP_SETTINGS)
class OtpUiTests(TestCase):
    def setUp(self):
        cache.clear(); outbox.clear()
        self.user = get_user_model().objects.create_user("sms-ui", password="secret-pass-123")
        self.other = get_user_model().objects.create_user("sms-other", password="secret-pass-123")
        Profile.objects.create(user=self.user, phone="09121234567")

    def code(self):
        return re.search(r"\b[0-9]{6}\b", outbox[-1]["body"]).group(0)

    def verify_identity(self, phone="09121234567"):
        self.client.force_login(self.user)
        response = self.client.post(reverse("accounts:phone_verification_request"), {"phone": phone})
        return response

    def test_password_mode_copy_and_safe_next_are_preserved(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, "Sign in with SMS code")
        self.assertNotContains(response, "Username or email")
        safe = self.client.post(reverse("accounts:login") + "?next=/en/accounts/", {"username": "sms-ui", "password": "secret-pass-123"})
        self.assertRedirects(safe, "/en/accounts/")
        self.client.logout()
        external = self.client.post(reverse("accounts:login") + "?next=https://evil.example", {"username": "sms-ui", "password": "secret-pass-123"})
        self.assertNotEqual(external["Location"], "https://evil.example")

    def test_phone_verification_profile_separation_and_sms_login_session(self):
        response = self.verify_identity()
        challenge_id = response["Location"].rstrip("/").split("/")[-1]
        response = self.client.post(reverse("accounts:phone_verification_verify", args=[challenge_id]), {"code": self.code()}, follow=True)
        self.assertContains(response, "Phone verified")
        self.assertEqual(PhoneIdentity.objects.get(user=self.user).phone_e164, "+989121234567")
        self.assertEqual(Profile.objects.get(user=self.user).phone, "09121234567")
        self.client.logout(); cache.clear(); outbox.clear()
        response = self.client.post(reverse("accounts:sms_login_request") + "?next=/en/accounts/", {"phone": "09121234567"})
        login_id = response["Location"].rstrip("/").split("/")[-1]
        response = self.client.post(reverse("accounts:sms_login_verify", args=[login_id]), {"code": self.code()})
        self.assertRedirects(response, "/en/accounts/")
        self.assertEqual(self.client.session.get("_auth_user_id"), str(self.user.id))

    def test_unknown_and_inactive_are_generic_and_never_log_in(self):
        known = self.client.post(reverse("accounts:sms_login_request"), {"phone": "09121111111"})
        unknown_id = known["Location"].rstrip("/").split("/")[-1]
        self.assertIsNone(OtpChallenge.objects.get(id=unknown_id).user)
        failed = self.client.post(reverse("accounts:sms_login_verify", args=[unknown_id]), {"code": "123456"})
        self.assertContains(failed, "invalid or has expired")
        cache.clear()
        PhoneIdentity.objects.create(user=self.other, phone_e164="+989121111111", verified_at=timezone.now())
        self.other.is_active = False; self.other.save()
        response = self.client.post(reverse("accounts:sms_login_request"), {"phone": "09121111111"})
        inactive_id = response["Location"].rstrip("/").split("/")[-1]
        self.assertIsNone(OtpChallenge.objects.get(id=inactive_id).user)

    def test_cross_user_phone_challenge_cannot_transfer_identity(self):
        response = self.verify_identity("09121111111")
        challenge_id = response["Location"].rstrip("/").split("/")[-1]
        code = self.code(); self.client.force_login(self.other)
        denied = self.client.post(reverse("accounts:phone_verification_verify", args=[challenge_id]), {"code": code})
        self.assertContains(denied, "invalid or has expired")
        self.assertFalse(PhoneIdentity.objects.filter(user=self.other).exists())
        self.client.force_login(self.user)
        accepted = self.client.post(reverse("accounts:phone_verification_verify", args=[challenge_id]), {"code": code})
        self.assertRedirects(accepted, reverse("accounts:profile"))


@override_settings(SMS_ENABLED=False, SMS_OTP_LOGIN_ENABLED=False)
class SmsUiDisabledTests(TestCase):
    def test_sms_routes_and_profile_control_are_hidden(self):
        user = get_user_model().objects.create_user("off-ui", password="secret-pass-123")
        Profile.objects.create(user=user)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("accounts:sms_login_request")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("accounts:profile")), "Phone for SMS login")
