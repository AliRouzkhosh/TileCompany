import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import OtpChallenge, PhoneIdentity, Profile
from .sms import outbox


@override_settings(SMS_ENABLED=True, SMS_OTP_LOGIN_ENABLED=True, SMS_BACKEND="accounts.sms.MemorySmsBackend")
class IdentityCollisionUxTests(TestCase):
    def setUp(self):
        cache.clear(); outbox.clear()

    def grant(self, phone):
        session = self.client.session
        session["sms_signup_grant"] = {"phone": phone, "expires": (timezone.now() + timedelta(minutes=10)).timestamp()}
        session.save()

    def payload(self, username, email=""):
        return {"username": username, "email": email, "password1": "valid-password-123", "password2": "valid-password-123"}

    def test_existing_verified_phone_is_detected_only_after_signup_otp_proof_without_grant(self):
        owner = get_user_model().objects.create_user("owner", password="valid-password-123")
        Profile.objects.create(user=owner)
        PhoneIdentity.objects.create(user=owner, phone_e164="+989121234567", verified_at=timezone.now())
        requested = self.client.post("/en/accounts/register/sms/", {"phone": "09121234567"})
        self.assertEqual(requested.status_code, 302)
        challenge_url = requested["Location"]
        code = re.search(r"\b[0-9]{6}\b", outbox[-1]["body"]).group(0)
        result = self.client.post(challenge_url, {"code": code})
        self.assertContains(result, "This number is already verified for an account.")
        self.assertContains(result, "/en/accounts/login/sms/")
        self.assertContains(result, "/en/accounts/login/")
        self.assertNotIn("sms_signup_grant", self.client.session)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_shared_case_insensitive_duplicate_email_validation_for_standard_and_sms(self):
        existing = get_user_model().objects.create_user("email-owner", email="User@Example.com", password="valid-password-123")
        Profile.objects.create(user=existing)
        standard = self.client.post("/en/accounts/register/", self.payload("standard-duplicate", "user@example.com"))
        self.assertEqual(standard.status_code, 200)
        self.assertContains(standard, "This email is already used for an account.")
        self.grant("+989121234568")
        sms = self.client.post("/en/accounts/register/sms/complete/", self.payload("sms-duplicate", "USER@example.com"))
        self.assertEqual(sms.status_code, 200)
        self.assertContains(sms, "This email is already used for an account.")
        self.assertFalse(get_user_model().objects.filter(username="sms-duplicate").exists())

    def test_change_number_links_are_get_only_and_masked_for_all_verification_purposes(self):
        user = get_user_model().objects.create_user("profile-owner", password="valid-password-123")
        Profile.objects.create(user=user)
        challenges = {
            "login": OtpChallenge.objects.create(purpose="login", phone_e164="+989121111111", code_hash="x", expires_at=timezone.now() + timedelta(minutes=2)),
            "signup": OtpChallenge.objects.create(purpose="signup", phone_e164="+989121222222", code_hash="x", expires_at=timezone.now() + timedelta(minutes=2)),
            "verify": OtpChallenge.objects.create(purpose="verify_phone", phone_e164="+989121333333", code_hash="x", expires_at=timezone.now() + timedelta(minutes=2), user=user),
        }
        login = self.client.get(f"/en/accounts/login/sms/{challenges['login'].id}/")
        signup = self.client.get(f"/en/accounts/register/sms/{challenges['signup'].id}/")
        self.client.force_login(user)
        profile = self.client.get(f"/en/accounts/profile/phone/{challenges['verify'].id}/")
        for response in (login, signup, profile):
            self.assertContains(response, "Code sent to")
            self.assertContains(response, "Change number")
        self.assertContains(login, "/en/accounts/login/sms/")
        self.assertContains(signup, "/en/accounts/register/sms/")
        self.assertContains(profile, "/en/accounts/profile/phone/")
        self.assertEqual(len(outbox), 0)
        challenges["signup"].refresh_from_db(); self.assertTrue(challenges["signup"].is_active)
