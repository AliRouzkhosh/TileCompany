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
