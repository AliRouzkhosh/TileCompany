from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError


class ReadinessViewTests(SimpleTestCase):
    def test_configured_cache_round_trip_marks_process_ready_without_authentication(self):
        response = self.client.get(reverse("readiness"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ready")
        self.assertEqual(response["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")

    def test_cache_connection_failure_returns_generic_service_unavailable(self):
        secret_url = "redis://:not-for-response@redis.internal:6379/0"
        with patch("TileCompany.health.cache.set", side_effect=RedisConnectionError(secret_url)):
            response = self.client.get(reverse("readiness"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable")
        self.assertNotIn(b"not-for-response", response.content)
        self.assertNotIn(b"redis.internal", response.content)
        self.assertNotIn(b"6379", response.content)

    def test_cache_probe_requires_a_verified_round_trip(self):
        with patch("TileCompany.health.cache.get", return_value="unexpected"):
            response = self.client.get(reverse("readiness"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable")
