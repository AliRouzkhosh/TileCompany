"""Regression coverage for production-safe logging helpers."""

import io
import logging

from django.conf import settings
from django.test import SimpleTestCase

from TileCompany.logging import RedactingFormatter, SensitiveDataRedactionFilter
from TileCompany.security_events import security_event


class ProductionLoggingTests(SimpleTestCase):
    def _logger_with_capture(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(SensitiveDataRedactionFilter())
        handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s"))
        logger = logging.getLogger("TileCompany.tests.logging")
        logger.handlers = [handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        return logger, stream

    def test_settings_configure_redacting_console_logging(self):
        console = settings.LOGGING["handlers"]["console"]
        self.assertEqual(console["filters"], ["redact_sensitive"])
        self.assertEqual(console["formatter"], "console")
        self.assertEqual(settings.LOGGING["root"]["level"], "WARNING")

    def test_mapping_arguments_and_exception_fields_are_redacted(self):
        logger, stream = self._logger_with_capture()
        logger.warning("provider diagnostic %(payload)s", {"payload": {"password": "not-for-logs", "token": "also-secret"}})
        try:
            raise RuntimeError("authorization=Bearer-not-for-logs password=not-for-logs")
        except RuntimeError:
            logger.exception("provider request failed")

        output = stream.getvalue()
        self.assertNotIn("not-for-logs", output)
        self.assertIn("<redacted>", output)

    def test_security_events_use_a_pseudonymous_subject(self):
        logger, stream = self._logger_with_capture()
        security_logger = logging.getLogger("TileCompany.security")
        previous_handlers, previous_propagate = security_logger.handlers, security_logger.propagate
        security_logger.handlers, security_logger.propagate = logger.handlers, False
        try:
            security_event("password_login_throttled", subject="person@example.test")
        finally:
            security_logger.handlers, security_logger.propagate = previous_handlers, previous_propagate

        output = stream.getvalue()
        self.assertIn("security_event=password_login_throttled", output)
        self.assertNotIn("person@example.test", output)

    def test_django_request_warning_redacts_only_sensitive_auth_path_values(self):
        logger, stream = self._logger_with_capture()
        logger.name = "django.request"
        challenge = "11111111-1111-4111-8111-111111111111"
        reset_token = "synthetic-reset-token"
        sensitive_paths = (
            f"/en/accounts/reset/synthetic-uid/{reset_token}/",
            f"/fa/accounts/reset/synthetic-uid/{reset_token}/",
            f"/en/accounts/login/sms/{challenge}/",
            f"/fa/accounts/login/sms/{challenge}/resend/",
            f"/en/accounts/register/sms/{challenge}/",
            f"/fa/accounts/register/sms/{challenge}/resend/",
            f"/en/accounts/profile/phone/{challenge}/",
            f"/fa/accounts/profile/phone/{challenge}/resend/",
        )

        for path in sensitive_paths:
            with self.subTest(path=path):
                logger.warning("Not Found: %s", path)
                output = stream.getvalue()
                self.assertNotIn(challenge, output)
                self.assertNotIn(reset_token, output)
                self.assertIn("[redacted]", output)

        self.assertIn("WARNING Not Found:", stream.getvalue())

    def test_django_request_logging_keeps_ordinary_paths_visible_and_removes_query_strings(self):
        logger, stream = self._logger_with_capture()
        ordinary_paths = (
            "/en/accounts/login/",
            "/en/accounts/register/",
            "/en/accounts/register/sms/complete/",
            "/en/accounts/reset/done/",
            "/en/accounts/login/sms/not-a-uuid/",
            "/en/shop/normal-item/",
            "/some-normal-missing-page/",
        )

        for path in ordinary_paths:
            with self.subTest(path=path):
                logger.warning("Not Found: %s", path)
                self.assertIn(path, stream.getvalue())

        query_marker = "observability-query-marker"
        logger.warning("Not Found: %s", f"/en/shop/?marker={query_marker}")
        output = stream.getvalue()
        self.assertIn("/en/shop/", output)
        self.assertNotIn(query_marker, output)
