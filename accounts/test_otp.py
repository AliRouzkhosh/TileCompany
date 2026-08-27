from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.checks import run_checks
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import OtpChallenge, PhoneIdentity, Profile
from .otp import (COOLDOWN, IP_SEND_LIMIT, IP_VERIFY_LIMIT, MAX_ATTEMPTS, OTP_TTL, PHONE_SEND_LIMIT, request_login_otp, request_phone_verification, verify_login_otp, verify_phone_otp)
from .phone_identity import normalize_phone_identity
from .sms import outbox
from .sms import send_sms


OTP_SETTINGS = {
    "SMS_ENABLED": True, "SMS_OTP_LOGIN_ENABLED": True,
    "SMS_BACKEND": "accounts.sms.MemorySmsBackend", "SMS_DEFAULT_REGION": "IR",
}


@override_settings(**OTP_SETTINGS)
class OtpFoundationTests(TestCase):
    def setUp(self):
        cache.clear(); outbox.clear()
        self.user = get_user_model().objects.create_user("otp-user", password="secret-pass-123")
        self.other = get_user_model().objects.create_user("otp-other", password="secret-pass-123")

    def code(self):
        return outbox[-1]["body"].split(": ", 1)[1].split("\n", 1)[0].split(".", 1)[0]

    def test_normalization_and_contact_phone_stays_untrusted(self):
        self.assertEqual(normalize_phone_identity("۰۹۱۲۱۲۳۴۵۶۷"), "+989121234567")
        self.assertEqual(normalize_phone_identity("989121234567"), "+989121234567")
        self.assertEqual(normalize_phone_identity("+1 202-555-0123"), "+12025550123")
        with self.assertRaises(ValidationError): normalize_phone_identity("123")
        Profile.objects.create(user=self.user, phone="09121234567")
        self.assertFalse(PhoneIdentity.objects.exists())

    def test_hash_storage_consumption_and_attempt_lock(self):
        result = request_phone_verification(user=self.user, raw_phone="09121234567", request_context={"ip": "10.0.0.1"})
        challenge = OtpChallenge.objects.get(id=result.challenge_id)
        code = self.code()
        self.assertEqual(len(code), 6)
        self.assertNotEqual(challenge.code_hash, code)
        for _ in range(4):
            self.assertFalse(verify_phone_otp(user=self.user, challenge_id=challenge.id, code="000000", request_context={"ip": "10.0.0.1"}).success)
        self.assertFalse(verify_phone_otp(user=self.user, challenge_id=challenge.id, code="000000", request_context={"ip": "10.0.0.1"}).success)
        challenge.refresh_from_db(); self.assertFalse(challenge.is_active); self.assertEqual(challenge.attempt_count, 5)
        self.assertFalse(verify_phone_otp(user=self.user, challenge_id=challenge.id, code=code, request_context={"ip": "10.0.0.1"}).success)

    def test_verified_identity_and_login_are_one_time(self):
        request = request_phone_verification(user=self.user, raw_phone="09121234567", request_context={"ip": "10.0.0.2"})
        self.assertTrue(verify_phone_otp(user=self.user, challenge_id=request.challenge_id, code=self.code(), request_context={"ip": "10.0.0.2"}).success)
        self.assertEqual(PhoneIdentity.objects.get(user=self.user).phone_e164, "+989121234567")
        login = request_login_otp(raw_phone="+989121234567", request_context={"ip": "10.0.0.3"})
        code = self.code()
        self.assertEqual(verify_login_otp(challenge_id=login.challenge_id, code=code, request_context={"ip": "10.0.0.3"}).user, self.user)
        self.assertFalse(verify_login_otp(challenge_id=login.challenge_id, code=code, request_context={"ip": "10.0.0.3"}).success)

    def test_unknown_login_is_opaque_and_cannot_authenticate(self):
        known = request_login_otp(raw_phone="09121234567", request_context={"ip": "10.0.0.4"})
        unknown = request_login_otp(raw_phone="09121111111", request_context={"ip": "10.0.0.5"})
        self.assertTrue(known.challenge_id); self.assertTrue(unknown.challenge_id)
        self.assertIsNone(OtpChallenge.objects.get(id=unknown.challenge_id).user)
        self.assertFalse(verify_login_otp(challenge_id=unknown.challenge_id, code="123456", request_context={"ip": "10.0.0.5"}).success)

    def test_resend_invalidates_and_limits(self):
        first = request_phone_verification(user=self.user, raw_phone="09121234567", request_context={"ip": "10.0.0.6"})
        second = request_phone_verification(user=self.user, raw_phone="+989121234567", request_context={"ip": "10.0.0.6"})
        self.assertEqual(second.retry_after, COOLDOWN)
        cache.clear()  # test only: simulate expiry of cooldown, not the send bucket.
        # This validates atomic replacement independently of the cache window.
        newer = request_phone_verification(user=self.user, raw_phone="+989121234567", request_context={"ip": "10.0.0.7"})
        old = OtpChallenge.objects.get(id=first.challenge_id); old.refresh_from_db()
        self.assertFalse(old.is_active); self.assertTrue(newer.challenge_id)

    def test_phone_cannot_be_stolen(self):
        PhoneIdentity.objects.create(user=self.other, phone_e164="+989121234567", verified_at=timezone.now())
        with self.assertRaises(ValidationError):
            request_phone_verification(user=self.user, raw_phone="09121234567", request_context={"ip": "10.0.0.8"})


@override_settings(**OTP_SETTINGS)
class OtpContractSecurityTests(TestCase):
    def setUp(self):
        cache.clear(); outbox.clear()
        self.user = get_user_model().objects.create_user("security-user", password="secret-pass-123")
        self.other = get_user_model().objects.create_user("security-other", password="secret-pass-123")

    def request_phone(self, phone="09121234567", ip="192.168.23.4", **kwargs):
        return request_phone_verification(user=self.user, raw_phone=phone, request_context={"ip": ip}, **kwargs)

    def code(self):
        body = outbox[-1]["body"]
        return body.split(": ", 1)[1].split("\n", 1)[0].split(".", 1)[0]

    def verify_phone(self, result, code=None, ip="192.168.23.4"):
        return verify_phone_otp(user=self.user, challenge_id=result.challenge_id, code=code or self.code(), request_context={"ip": ip})

    def make_identity(self, user=None, phone="+989121234567"):
        return PhoneIdentity.objects.create(user=user or self.user, phone_e164=phone, verified_at=timezone.now())

    def test_locked_constants_and_normalization_matrix(self):
        self.assertEqual((OTP_TTL, COOLDOWN, PHONE_SEND_LIMIT, IP_SEND_LIMIT, MAX_ATTEMPTS, IP_VERIFY_LIMIT), (timedelta(minutes=2), 120, 3, 10, 5, 20))
        for value in ("09121234567", "989121234567", "+989121234567", "٠٩١٢١٢٣٤٥٦٧"):
            self.assertEqual(normalize_phone_identity(value), "+989121234567")
        self.assertEqual(normalize_phone_identity("+12025550123"), "+12025550123")
        for value in ("123", "0912123456", "999999999999999999999999", "004412345678"):
            with self.assertRaises(ValidationError): normalize_phone_identity(value)

    def test_profile_and_address_are_never_identity_authority(self):
        profile = Profile.objects.create(user=self.user, phone="09121234567")
        self.make_identity()
        profile.phone = "09999999999"; profile.save()
        self.assertEqual(PhoneIdentity.objects.get(user=self.user).phone_e164, "+989121234567")
        self.assertEqual(Profile.objects.filter(phone="09999999999").count(), 1)
        # Duplicate and blank contact values remain legal legacy contact data.
        Profile.objects.create(user=self.other, phone="09999999999")
        self.assertTrue(Profile.objects.filter(phone="09999999999").count() == 2)

    def test_hash_storage_and_cryptographic_generation(self):
        request = self.request_phone()
        challenge = OtpChallenge.objects.get(id=request.challenge_id)
        code = self.code()
        self.assertRegex(code, r"^[0-9]{6}$")
        self.assertIn("pbkdf2_", challenge.code_hash)
        self.assertNotIn(code, challenge.code_hash)
        self.assertNotIn("code", {field.name for field in OtpChallenge._meta.fields} - {"code_hash"})
        with patch("accounts.otp.secrets.randbelow", return_value=42) as secure_source:
            cache.clear(); outbox.clear()
            request_phone_verification(user=self.other, raw_phone="09121111111", request_context={"ip": "192.168.23.5"})
            self.assertIn("000042", outbox[-1]["body"]); secure_source.assert_called_once_with(1_000_000)

    def test_expiry_purpose_isolation_and_tampering(self):
        request = self.request_phone()
        challenge = OtpChallenge.objects.get(id=request.challenge_id)
        code = self.code()
        with patch("accounts.otp.timezone.now", return_value=challenge.expires_at + timedelta(seconds=1)):
            self.assertFalse(self.verify_phone(request, code).success)
        self.assertFalse(verify_login_otp(challenge_id=request.challenge_id, code=code, request_context={"ip": "192.168.23.4"}).success)
        self.assertFalse(verify_phone_otp(user=self.user, challenge_id="00000000-0000-0000-0000-000000000000", code=code, request_context={"ip": "192.168.23.4"}).success)

    def test_wrong_attempt_counter_and_success_are_idempotent(self):
        request = self.request_phone()
        for expected in range(1, 5):
            self.assertFalse(self.verify_phone(request, "111111").success)
            self.assertEqual(OtpChallenge.objects.get(id=request.challenge_id).attempt_count, expected)
        code = self.code()
        self.assertTrue(self.verify_phone(request, code).success)
        self.assertFalse(self.verify_phone(request, code).success)

    def test_phone_window_and_ip_window_are_server_side(self):
        # Clearing only cooldown models time passage while retaining the rate bucket.
        for phone in ("09121234567", "+989121234567", "989121234567"):
            self.request_phone(phone=phone, ip="192.168.23.6")
            cache.delete("otp:cooldown:" + __import__("accounts.otp", fromlist=["_fingerprint"])._fingerprint("+989121234567:verify_phone"))
        with self.assertRaises(PermissionDenied): self.request_phone(ip="192.168.23.6")
        cache.clear()
        for number in range(10):
            request_login_otp(raw_phone=f"+98912{number:07d}", request_context={"ip": "192.168.23.7"})
        with self.assertRaises(PermissionDenied): request_login_otp(raw_phone="+989129999999", request_context={"ip": "192.168.23.7"})

    @override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
    def test_verify_ip_limit_and_privacy_fingerprints(self):
        # New unknown challenges give twenty independent, non-authenticating attempts.
        for number in range(IP_VERIFY_LIMIT):
            request = request_login_otp(raw_phone=f"+98911{number:07d}", request_context={"ip": f"10.0.0.{number}"})
            self.assertFalse(verify_login_otp(challenge_id=request.challenge_id, code="000000", request_context={"ip": "2001:db8::1"}).success)
        request = request_login_otp(raw_phone="+989129999999", request_context={"ip": "10.0.0.99"})
        self.assertFalse(verify_login_otp(challenge_id=request.challenge_id, code="000000", request_context={"ip": "2001:db8::1"}).success)
        challenge = OtpChallenge.objects.get(id=request.challenge_id)
        self.assertNotIn("10.0.0.99", challenge.request_ip_fingerprint)
        self.assertNotIn("+989", __import__("accounts.otp", fromlist=["_fingerprint"])._fingerprint("+989121234567"))

    def test_known_unknown_inactive_flows_are_uniform_and_rechecked(self):
        self.make_identity(); self.other.is_active = False; self.other.save()
        PhoneIdentity.objects.create(user=self.other, phone_e164="+98912111111", verified_at=timezone.now())
        known = request_login_otp(raw_phone="09121234567", request_context={"ip": "10.1.0.1"})
        unknown = request_login_otp(raw_phone="09121333333", request_context={"ip": "10.1.0.2"})
        inactive = request_login_otp(raw_phone="09121111111", request_context={"ip": "10.1.0.3"})
        for result in (known, unknown, inactive):
            self.assertEqual(set(result.__dict__), {"challenge_id", "retry_after", "delivery_failed", "already_verified"})
            self.assertRegex(result.challenge_id, r"^[0-9a-f-]{36}$")
        code = self.code()
        PhoneIdentity.objects.filter(user=self.user).update(phone_e164="+989121234568")
        self.assertFalse(verify_login_otp(challenge_id=known.challenge_id, code=code, request_context={"ip": "10.1.0.1"}).success)
        self.assertFalse(verify_login_otp(challenge_id=unknown.challenge_id, code="000000", request_context={"ip": "10.1.0.2"}).success)

    def test_phone_completion_replacement_and_conflict(self):
        self.make_identity(phone="+989121234567")
        request = self.request_phone(phone="09121111111", ip="10.2.0.1")
        self.assertTrue(self.verify_phone(request, ip="10.2.0.1").success)
        self.assertEqual(PhoneIdentity.objects.get(user=self.user).phone_e164, "+989121111111")
        cache.clear(); outbox.clear()
        old_login = request_login_otp(raw_phone="09121234567", request_context={"ip": "10.2.0.2"})
        new_login = request_login_otp(raw_phone="09121111111", request_context={"ip": "10.2.0.3"})
        self.assertIsNone(OtpChallenge.objects.get(id=old_login.challenge_id).user)
        self.assertTrue(OtpChallenge.objects.get(id=new_login.challenge_id).user_id == self.user.id)
        self.assertTrue(request_phone_verification(user=self.user, raw_phone="09121111111", request_context={"ip": "10.2.0.4"}).already_verified)

    @override_settings(SMS_BACKEND="accounts.sms.FailingSmsBackend")
    def test_send_failure_is_terminal_and_safe(self):
        request = self.request_phone()
        challenge = OtpChallenge.objects.get(id=request.challenge_id)
        self.assertTrue(request.delivery_failed); self.assertFalse(challenge.is_active)
        self.assertEqual(challenge.send_error_class, "SimulatedSmsFailure")
        self.assertFalse(self.verify_phone(request, "000000").success)

    def test_messages_and_memory_backend_contract(self):
        fa = self.request_phone(language="fa")
        self.assertEqual(outbox[-1]["to"], "+989121234567"); self.assertEqual(outbox[-1]["purpose"], "verify_phone")
        self.assertIn("کد تأیید", outbox[-1]["body"])
        cache.clear(); outbox.clear()
        self.make_identity()
        request_login_otp(raw_phone="09121234567", language="en", request_context={"ip": "10.3.0.1"})
        self.assertIn("Your login code", outbox[-1]["body"])
        self.assertRegex(outbox[-1]["body"], r"[0-9]{6}")


class OtpConfigurationAndPurgeTests(TestCase):
    def setUp(self):
        cache.clear(); outbox.clear()
        self.user = get_user_model().objects.create_user("purge-user", password="secret-pass-123")

    @override_settings(SMS_ENABLED=False, SMS_OTP_LOGIN_ENABLED=False)
    def test_disabled_fails_closed_and_default_production_checks_pass(self):
        with self.assertRaises(PermissionDenied): request_login_otp(raw_phone="09121234567", request_context={"ip": "1.1.1.1"})
        self.assertFalse(outbox); self.assertFalse(OtpChallenge.objects.exists())
        self.assertFalse([x for x in run_checks() if x.id.startswith("accounts.E")])

    @override_settings(DEBUG=False, SMS_ENABLED=True, SMS_OTP_LOGIN_ENABLED=True, SMS_BACKEND="accounts.sms.MemorySmsBackend")
    def test_production_fake_backend_and_local_cache_are_blocking(self):
        ids = {x.id for x in run_checks()}
        self.assertIn("accounts.E002", ids); self.assertIn("accounts.E003", ids)

    @override_settings(DEBUG=False, SMS_ENABLED=True, SMS_OTP_LOGIN_ENABLED=True, SMS_BACKEND="accounts.sms.ConsoleSmsBackend")
    def test_production_console_backend_is_blocking(self):
        self.assertIn("accounts.E002", {x.id for x in run_checks()})

    @override_settings(DEBUG=False, SMS_ENABLED=True, SMS_OTP_LOGIN_ENABLED=True, SMS_BACKEND="accounts.sms.DisabledSmsBackend")
    def test_production_requires_shared_cache_even_without_fake_backend(self):
        self.assertIn("accounts.E003", {x.id for x in run_checks()})

    @override_settings(SMS_ENABLED=False, SMS_OTP_LOGIN_ENABLED=True)
    def test_inconsistent_flags_are_blocking(self):
        self.assertIn("accounts.E001", {x.id for x in run_checks()})

    def test_console_and_purge_retention(self):
        now = timezone.now()
        active = OtpChallenge.objects.create(purpose="login", phone_e164="+989121234567", code_hash="x", expires_at=now + timedelta(minutes=5))
        old_consumed = OtpChallenge.objects.create(purpose="login", phone_e164="+989121234568", code_hash="x", expires_at=now - timedelta(days=8), is_active=False, consumed_at=now - timedelta(days=8))
        recent = OtpChallenge.objects.create(purpose="login", phone_e164="+989121234569", code_hash="x", expires_at=now - timedelta(days=1), is_active=False)
        OtpChallenge.objects.filter(id=old_consumed.id).update(created_at=now - timedelta(days=8))
        output = StringIO(); call_command("purge_old_otp_challenges", "--dry-run", stdout=output)
        self.assertTrue(OtpChallenge.objects.filter(id=old_consumed.id).exists())
        call_command("purge_old_otp_challenges", stdout=StringIO())
        self.assertTrue(OtpChallenge.objects.filter(id=active.id).exists()); self.assertTrue(OtpChallenge.objects.filter(id=recent.id).exists())
        self.assertFalse(OtpChallenge.objects.filter(id=old_consumed.id).exists())

    @override_settings(SMS_ENABLED=True, SMS_BACKEND="accounts.sms.ConsoleSmsBackend", DEBUG=True)
    def test_console_backend_is_explicitly_development_only(self):
        with patch("builtins.print") as printed:
            result = send_sms(to="+989121234567", message="Your login code is 123456.", purpose="login")
        self.assertTrue(result.accepted)
        self.assertIn("[DEV SMS]", printed.call_args.args[0])
