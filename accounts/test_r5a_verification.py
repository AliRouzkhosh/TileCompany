"""R5A verification-only evidence: isolated DB, rendered HTML, and resend semantics."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import OtpChallenge, Profile
from .otp import _fingerprint, request_phone_verification, verify_phone_otp
from .sms import outbox


@override_settings(SMS_ENABLED=True, SMS_OTP_LOGIN_ENABLED=True, SMS_BACKEND="accounts.sms.MemorySmsBackend")
class R5aLocalizationAndResendVerification(TestCase):
    def setUp(self):
        cache.clear(); outbox.clear()
        self.user = get_user_model().objects.create_user("r5a", password="secret-pass-123")
        Profile.objects.create(user=self.user)

    def challenge(self, purpose, phone):
        return OtpChallenge.objects.create(purpose=purpose, phone_e164=phone, code_hash="pbkdf2_sha256$1$bad$bad", expires_at=timezone.now() + timedelta(minutes=2), user=self.user if purpose == "verify_phone" else None)

    def test_fa_and_en_rendered_html(self):
        login = self.challenge("login", "+989121234567")
        signup = self.challenge("signup", "+989121111111")
        session = self.client.session; session["sms_signup_grant"] = {"phone": "+989121111111", "expires": (timezone.now() + timedelta(minutes=10)).timestamp()}; session.save()
        self.client.force_login(self.user)
        phone = self.challenge("verify_phone", "+989121222222")
        signup_fa = self.client.get(f"/fa/accounts/register/sms/{signup.id}/").content.decode()
        for text in ("کد ورود را وارد کنید", "این کد تا ۲ دقیقه معتبر است.", "کد تأیید", "تأیید شماره تلفن", "ثبت‌نام عادی"):
            self.assertIn(text, signup_fa)
        self.assertNotIn("This code is valid for 5 minutes.", signup_fa)
        self.assertIn("ارسال مجدد کد در %(seconds)s ثانیه", signup_fa)
        signup_en = self.client.get(f"/en/accounts/register/sms/{signup.id}/").content.decode()
        self.assertIn("This code is valid for 2 minutes.", signup_en)
        self.assertIn("Resend code in %(seconds)s seconds", signup_en)

    def test_refresh_is_timestamp_based_and_resend_post_invalidates_old(self):
        result = request_phone_verification(user=self.user, raw_phone="09121234567", request_context={"ip": "10.0.0.1"})
        old = OtpChallenge.objects.get(id=result.challenge_id); old.created_at = timezone.now() - timedelta(seconds=25); old.save(update_fields=["created_at"])
        self.client.force_login(self.user)
        page = self.client.get(reverse("accounts:phone_verification_verify", args=[old.id]))
        self.assertIn("data-resend-deadline", page.content.decode())
        early = self.client.post(reverse("accounts:phone_verification_resend", args=[old.id]))
        self.assertEqual(early.status_code, 302)
        cache.delete("otp:cooldown:" + _fingerprint(old.phone_e164 + ":verify_phone"))
        legal = self.client.post(reverse("accounts:phone_verification_resend", args=[old.id]))
        self.assertEqual(legal.status_code, 302)
        new_id = legal["Location"].rstrip("/").split("/")[-1]
        old.refresh_from_db(); self.assertFalse(old.is_active)
        self.assertNotEqual(str(old.id), new_id)
