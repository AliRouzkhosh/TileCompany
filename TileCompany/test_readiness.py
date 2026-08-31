from unittest.mock import MagicMock, patch

from django.db import OperationalError
from django.test import SimpleTestCase
from django.urls import reverse
from redis.exceptions import ConnectionError as RedisConnectionError


class ReadinessViewTests(SimpleTestCase):
    def successful_database_cursor(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        cursor_context = MagicMock()
        cursor_context.__enter__.return_value = cursor
        database = MagicMock()
        database.cursor.return_value = cursor_context
        return database

    def test_configured_cache_round_trip_marks_process_ready_without_authentication(self):
        with patch("TileCompany.health.connection", self.successful_database_cursor()):
            response = self.client.get(reverse("readiness"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ready")
        self.assertEqual(response["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")

    def test_cache_connection_failure_returns_generic_service_unavailable(self):
        secret_url = "redis://:not-for-response@redis.internal:6379/0"
        with (
            patch("TileCompany.health.connection", self.successful_database_cursor()),
            patch("TileCompany.health.cache.set", side_effect=RedisConnectionError(secret_url)),
            self.assertLogs("TileCompany.health", level="WARNING") as logs,
        ):
            response = self.client.get(reverse("readiness"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable")
        self.assertNotIn(b"not-for-response", response.content)
        self.assertNotIn(b"redis.internal", response.content)
        self.assertNotIn(b"6379", response.content)
        self.assertEqual(logs.output, ["WARNING:TileCompany.health:Readiness cache probe unavailable"])

    def test_cache_probe_requires_a_verified_round_trip(self):
        with (
            patch("TileCompany.health.connection", self.successful_database_cursor()),
            patch("TileCompany.health.cache.get", return_value="unexpected"),
        ):
            response = self.client.get(reverse("readiness"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable")

    def test_database_failure_returns_generic_service_unavailable_without_sensitive_logging(self):
        secret_url = "postgresql://fake-user:fake-password@db.internal:5432/tilecompany"
        with (
            patch("TileCompany.health.connection") as database,
            self.assertLogs("TileCompany.health", level="WARNING") as logs,
        ):
            database.cursor.side_effect = OperationalError(secret_url)
            response = self.client.get(reverse("readiness"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"unavailable")
        self.assertNotIn(b"fake-user", response.content)
        self.assertNotIn(b"fake-password", response.content)
        self.assertNotIn(b"db.internal", response.content)
        self.assertEqual(logs.output, ["WARNING:TileCompany.health:Readiness database probe unavailable"])
