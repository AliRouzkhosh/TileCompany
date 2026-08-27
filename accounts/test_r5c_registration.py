from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import OtpChallenge, PhoneIdentity, Profile


@override_settings(SMS_ENABLED=True, SMS_OTP_LOGIN_ENABLED=True, SMS_BACKEND="accounts.sms.MemorySmsBackend", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class RegistrationParityTests(TestCase):
    profile_values = {
        "birthdate": "1990-01-02",
        "gender": "female",
        "is_business": "on",
        "company_name": "Example Tile Co",
        "company_tax_id": "VAT-123",
    }

    def grant(self, phone="+989121234567", *, expired=False):
        session = self.client.session
        session["sms_signup_grant"] = {
            "phone": phone,
            "expires": (timezone.now() + timedelta(minutes=-1 if expired else 10)).timestamp(),
        }
        session.save()

    def completion_payload(self, username="sms-user", email="", **extra):
        return {
            "username": username,
            "email": email,
            "password1": "valid-password-123",
            "password2": "valid-password-123",
            **self.profile_values,
            **extra,
        }

    def test_standard_and_sms_signup_save_the_same_profile_contract(self):
        standard = self.client.post("/en/accounts/register/", self.completion_payload("standard-user", "standard@example.com"))
        self.assertEqual(standard.status_code, 302)
        standard_user = get_user_model().objects.get(username="standard-user")

        self.grant("+989121234567")
        sms = self.client.post("/en/accounts/register/sms/complete/", self.completion_payload("sms-user", "sms@example.com"))
        self.assertEqual(sms.status_code, 302)
        sms_user = get_user_model().objects.get(username="sms-user")
        for field in ("birthdate", "gender", "is_business", "company_name", "company_tax_id"):
            self.assertEqual(getattr(standard_user.profile, field), getattr(sms_user.profile, field))
        self.assertTrue(sms_user.check_password("valid-password-123"))
        self.assertEqual(PhoneIdentity.objects.get(user=sms_user).phone_e164, "+989121234567")

    def test_sms_email_is_optional_and_phone_only_comes_from_the_grant(self):
        self.grant("+989121234568")
        response = self.client.post("/en/accounts/register/sms/complete/", self.completion_payload("no-email-user", phone="+989199999999"))
        self.assertEqual(response.status_code, 302)
        user = get_user_model().objects.get(username="no-email-user")
        self.assertEqual(user.email, "")
        self.assertEqual(PhoneIdentity.objects.get(user=user).phone_e164, "+989121234568")
        self.assertEqual(len(mail.outbox), 0)

    def test_completion_rejects_expired_or_duplicate_phone_grants_without_partial_account(self):
        self.grant(expired=True)
        self.assertEqual(self.client.get("/en/accounts/register/sms/complete/").status_code, 404)

        owner = get_user_model().objects.create_user("phone-owner", password="valid-password-123")
        Profile.objects.create(user=owner)
        PhoneIdentity.objects.create(user=owner, phone_e164="+989121234569", verified_at=timezone.now())
        self.grant("+989121234569")
        response = self.client.post("/en/accounts/register/sms/complete/", self.completion_payload("blocked-user"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username="blocked-user").exists())

    def test_atomic_sms_completion_and_grant_one_time_use(self):
        self.grant("+989121234570")
        with patch("accounts.views.PhoneIdentity.objects.create", side_effect=IntegrityError("forced")):
            failed = self.client.post("/en/accounts/register/sms/complete/", self.completion_payload("atomic-user"))
        self.assertEqual(failed.status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username="atomic-user").exists())

        success = self.client.post("/en/accounts/register/sms/complete/", self.completion_payload("atomic-user"))
        self.assertEqual(success.status_code, 302)
        self.assertEqual(self.client.get("/en/accounts/register/sms/complete/").status_code, 404)

    def test_completion_renders_visible_shared_password_controls_and_keeps_validation_errors(self):
        self.grant("+989121234571")
        page = self.client.get("/fa/accounts/register/sms/complete/")
        self.assertContains(page, 'name="password1"')
        self.assertContains(page, 'name="password2"')
        self.assertEqual(page.content.decode().count('type="password"'), 2)
        self.assertContains(page, "data-jalali-birthdate")
        # The three Jalali date segments are now one grouped control rather
        # than three standalone auth controls.
        self.assertEqual(page.content.decode().count("sf-auth-control"), 7)
        invalid = self.client.post("/fa/accounts/register/sms/complete/", self.completion_payload("mismatch-user", password2="different-password-123"))
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, 'name="password1"')
        self.assertContains(invalid, 'name="password2"')

    def test_otp_verify_copy_uses_two_minutes_without_active_five_minute_text(self):
        challenge = OtpChallenge.objects.create(
            purpose="login", phone_e164="+989121234572", code_hash="x", expires_at=timezone.now() + timedelta(minutes=2)
        )
        page = self.client.get(f"/fa/accounts/login/sms/{challenge.id}/")
        self.assertContains(page, "کد شش‌رقمی را وارد کنید.")
        self.assertContains(page, "این کد تا ۲ دقیقه معتبر است.")
        self.assertNotContains(page, "پنج دقیقه")
