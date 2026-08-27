import threading
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, override_settings

from TileCompany.client_ip import get_client_ip

from .throttling import ThrottleUnavailable, allow, cache_key


class ClientIpResolutionTests(SimpleTestCase):
    def request(self, **meta):
        from django.test import RequestFactory
        return RequestFactory().get("/", **meta)

    @override_settings(TRUST_PROXY_CLIENT_IP=False)
    def test_remote_address_is_authoritative_without_proxy_trust(self):
        request = self.request(
            REMOTE_ADDR="203.0.113.10",
            HTTP_X_REAL_IP="198.51.100.4",
            HTTP_X_FORWARDED_FOR="198.51.100.5, 10.0.0.1",
        )
        self.assertEqual(get_client_ip(request), "203.0.113.10")

    @override_settings(TRUST_PROXY_CLIENT_IP=True)
    def test_enabled_proxy_trust_uses_only_normalized_single_header(self):
        request = self.request(
            REMOTE_ADDR="10.0.0.4",
            HTTP_X_REAL_IP="2001:db8::99",
            HTTP_X_FORWARDED_FOR="198.51.100.5, 10.0.0.1",
        )
        self.assertEqual(get_client_ip(request), "2001:db8::99")

    @override_settings(TRUST_PROXY_CLIENT_IP=True)
    def test_malformed_proxy_value_falls_back_without_parsing_forwarded_chain(self):
        request = self.request(
            REMOTE_ADDR="203.0.113.10",
            HTTP_X_REAL_IP="not-an-ip",
            HTTP_X_FORWARDED_FOR="198.51.100.5, 10.0.0.1",
        )
        self.assertEqual(get_client_ip(request), "203.0.113.10")

    @override_settings(TRUST_PROXY_CLIENT_IP=True)
    def test_invalid_remote_address_is_grouped_as_unknown(self):
        self.assertEqual(get_client_ip(self.request(REMOTE_ADDR="not-an-ip")), "unknown")


class CacheThrottleTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_fixed_window_limit_namespaces_and_privacy(self):
        subject = "+989121234567"
        key = cache_key("otp:send-phone", subject)
        self.assertNotIn(subject, key)
        self.assertEqual(key, cache_key("otp:send-phone", subject))
        self.assertTrue(allow(namespace="otp:send-phone", subject=subject, limit=2, window_seconds=60))
        self.assertTrue(allow(namespace="otp:send-phone", subject=subject, limit=2, window_seconds=60))
        self.assertFalse(allow(namespace="otp:send-phone", subject=subject, limit=2, window_seconds=60))
        self.assertTrue(allow(namespace="otp:verify-ip", subject=subject, limit=1, window_seconds=60))

    def test_deleted_window_resets_without_sleeping(self):
        subject = "203.0.113.10"
        self.assertTrue(allow(namespace="test", subject=subject, limit=1, window_seconds=60))
        self.assertFalse(allow(namespace="test", subject=subject, limit=1, window_seconds=60))
        cache.delete(cache_key("test", subject))
        self.assertTrue(allow(namespace="test", subject=subject, limit=1, window_seconds=60))

    def test_concurrent_calls_do_not_exceed_fixed_limit(self):
        barrier = threading.Barrier(8)
        results = []
        errors = []

        def worker():
            try:
                barrier.wait(timeout=2)
                results.append(allow(namespace="concurrent", subject="203.0.113.11", limit=3, window_seconds=60))
            except Exception as exc:  # surfaced by the main test thread
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sum(results), 3)

    def test_cache_failure_is_explicit_not_an_implicit_allow(self):
        with patch("accounts.throttling.cache.add", side_effect=OSError):
            with self.assertRaises(ThrottleUnavailable):
                allow(namespace="failure", subject="203.0.113.12", limit=1, window_seconds=60)

    def test_otp_verification_cache_failure_is_opaque(self):
        from .otp import _verify

        with patch("accounts.otp._limit", side_effect=PermissionDenied):
            self.assertIsNone(_verify(challenge_id="unused", code="000000", purpose="login", request_context={"ip": "203.0.113.12"}))
