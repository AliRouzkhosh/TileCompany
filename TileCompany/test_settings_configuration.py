"""Subprocess tests for startup-time configuration contracts."""
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_ENVIRONMENT_NAMES = {
    "APP_ENV", "DJANGO_SECRET_KEY", "DEBUG", "ALLOWED_HOSTS", "DB_BACKEND",
    "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT",
    "POSTGRES_CONNECT_TIMEOUT", "POSTGRES_SSLMODE", "POSTGRES_SSLROOTCERT",
    "EMAIL_BACKEND", "EMAIL_HOST", "EMAIL_PORT", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD",
    "EMAIL_USE_TLS", "EMAIL_USE_SSL", "DEFAULT_FROM_EMAIL", "SERVER_EMAIL", "SITE_URL",
    "STATIC_ROOT", "PRIVATE_INVENTORY_IMPORT_STORAGE_ROOT",
    "ZARINPAL_MERCHANT_ID", "ZARINPAL_SANDBOX", "ZARINPAL_DEVELOPMENT_AUTO_SUCCESS",
    "MOCK_PAYMENT_SIMULATION_ENABLED", "LOCAL_PAYMENT_SIMULATOR_ENABLED",
    "TRUST_PROXY_SSL_HEADER", "CSRF_TRUSTED_ORIGINS", "SECURE_HSTS_SECONDS",
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", "SECURE_HSTS_PRELOAD",
    "CACHE_BACKEND", "REDIS_URL", "TRUST_PROXY_CLIENT_IP", "SMS_ENABLED",
    "SMS_OTP_LOGIN_ENABLED", "SMS_BACKEND",
}


class ProductionSettingsContractTests(SimpleTestCase):
    def test_private_inventory_storage_root_is_explicit_and_absolute(self):
        private_root = PROJECT_ROOT / "private-imports"
        probe = self.settings_process(
            "from TileCompany import settings; "
            "assert settings.PRIVATE_INVENTORY_IMPORT_STORAGE_ROOT.name == 'private-imports'",
            APP_ENV="development",
            PRIVATE_INVENTORY_IMPORT_STORAGE_ROOT=str(private_root),
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assert_settings_failure(
            "PRIVATE_INVENTORY_IMPORT_STORAGE_ROOT must be an absolute path.",
            APP_ENV="development",
            PRIVATE_INVENTORY_IMPORT_STORAGE_ROOT="relative/imports",
        )

    def test_local_production_compose_allowlists_simulator_for_django_services(self):
        compose = (PROJECT_ROOT / "ops" / "local-prod" / "compose.yml").read_text(encoding="utf-8")
        example = (PROJECT_ROOT / "ops" / "local-prod" / ".env.example").read_text(encoding="utf-8")
        mapping = "LOCAL_PAYMENT_SIMULATOR_ENABLED: ${LOCAL_PAYMENT_SIMULATOR_ENABLED:-false}"
        self.assertEqual(compose.count(mapping), 2)
        self.assertIn("LOCAL_PAYMENT_SIMULATOR_ENABLED=false", example)

    def settings_process(self, code="import TileCompany.settings", **updates):
        environment = os.environ.copy()
        for name in CONFIG_ENVIRONMENT_NAMES:
            environment.pop(name, None)
        environment.update(updates)
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def production_environment(self, **updates):
        environment = {
            "APP_ENV": "production",
            "DJANGO_SECRET_KEY": "test-secret-key-with-sufficient-entropy-for-production-contract-12345",
            "DEBUG": "true",
            "ALLOWED_HOSTS": "example.test,www.example.test",
            "DB_BACKEND": "postgresql",
            "POSTGRES_DB": "tilecompany_test",
            "POSTGRES_USER": "tilecompany_test",
            "POSTGRES_PASSWORD": "test-password-not-a-production-secret",
            "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "EMAIL_HOST": "smtp.example.test",
            "EMAIL_PORT": "587",
            "EMAIL_USE_TLS": "true",
            "EMAIL_USE_SSL": "false",
            "DEFAULT_FROM_EMAIL": "Tile Company <no-reply@example.test>",
            "ZARINPAL_MERCHANT_ID": "00000000-0000-0000-0000-000000000001",
            "ZARINPAL_SANDBOX": "false",
            "ZARINPAL_DEVELOPMENT_AUTO_SUCCESS": "false",
            "MOCK_PAYMENT_SIMULATION_ENABLED": "false",
            "LOCAL_PAYMENT_SIMULATOR_ENABLED": "false",
            "CACHE_BACKEND": "redis",
            "REDIS_URL": "redis://:test-password@redis.example.test:6379/0",
        }
        environment.update(updates)
        return environment

    def local_production_environment(self, **updates):
        environment = {
            "APP_ENV": "local_production",
            "DJANGO_SECRET_KEY": "test-secret-key-with-sufficient-entropy-for-local-production-12345",
            "DEBUG": "true",
            "ALLOWED_HOSTS": "tilecompany.localhost,localhost",
            "DB_BACKEND": "postgresql",
            "POSTGRES_DB": "tilecompany_test",
            "POSTGRES_USER": "tilecompany_runtime",
            "POSTGRES_PASSWORD": "test-password-not-a-production-secret",
            "POSTGRES_HOST": "postgres",
            "POSTGRES_PORT": "5432",
            "CACHE_BACKEND": "redis",
            "REDIS_URL": "redis://:test-password@redis:6379/0",
        }
        environment.update(updates)
        return environment

    def assert_settings_failure(self, expected, **environment):
        result = self.settings_process(**environment)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected, result.stderr)

    def test_development_minimal_environment_loads(self):
        result = self.settings_process(APP_ENV="development")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_app_environment_fails_closed(self):
        self.assert_settings_failure("APP_ENV must be either", APP_ENV="unknown")

    def test_production_requires_secret_key(self):
        environment = self.production_environment(DJANGO_SECRET_KEY="")
        self.assert_settings_failure("DJANGO_SECRET_KEY is required", **environment)

    def test_production_forces_debug_false(self):
        environment = self.production_environment()
        probe = self.settings_process(
            "from TileCompany.settings import DEBUG; assert DEBUG is False",
            **environment,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)

    def test_production_requires_non_wildcard_hosts(self):
        self.assert_settings_failure("ALLOWED_HOSTS is required", **self.production_environment(ALLOWED_HOSTS=""))
        self.assert_settings_failure("must not contain '*'", **self.production_environment(ALLOWED_HOSTS="*"))

    def test_production_requires_postgresql_and_database_secrets(self):
        self.assert_settings_failure("requires DB_BACKEND=postgresql", **self.production_environment(DB_BACKEND="sqlite"))
        self.assert_settings_failure("POSTGRES_PASSWORD", **self.production_environment(POSTGRES_PASSWORD=""))

    def test_postgresql_connection_options_are_validated_and_environment_backed(self):
        configured = self.settings_process(
            "from TileCompany.settings import DATABASES; "
            "options = DATABASES['default']['OPTIONS']; "
            "assert options == {'connect_timeout': 7, 'sslmode': 'verify-full', 'sslrootcert': '/deployment/ca.pem'}",
            **self.production_environment(
                POSTGRES_CONNECT_TIMEOUT="7",
                POSTGRES_SSLMODE="verify-full",
                POSTGRES_SSLROOTCERT="/deployment/ca.pem",
            ),
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assert_settings_failure(
            "POSTGRES_CONNECT_TIMEOUT must be a positive integer",
            **self.production_environment(POSTGRES_CONNECT_TIMEOUT="0"),
        )
        self.assert_settings_failure(
            "POSTGRES_SSLMODE is not a supported PostgreSQL sslmode",
            **self.production_environment(POSTGRES_SSLMODE="invalid"),
        )

    def test_production_rejects_development_email_and_conflicting_transport(self):
        self.assert_settings_failure("non-development backend", **self.production_environment(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"))
        self.assert_settings_failure("cannot both be true", **self.production_environment(EMAIL_USE_TLS="true", EMAIL_USE_SSL="true"))

    def test_production_requires_live_payment_configuration(self):
        self.assert_settings_failure("ZARINPAL_SANDBOX must be explicitly", **self.production_environment(ZARINPAL_SANDBOX=""))
        self.assert_settings_failure("must be false", **self.production_environment(ZARINPAL_SANDBOX="true"))
        self.assert_settings_failure("ZARINPAL_MERCHANT_ID is required", **self.production_environment(ZARINPAL_MERCHANT_ID=""))
        self.assert_settings_failure(
            "Development payment simulation cannot be enabled in production.",
            **self.production_environment(MOCK_PAYMENT_SIMULATION_ENABLED="true"),
        )
        self.assert_settings_failure(
            "Development payment simulation cannot be enabled in production.",
            **self.production_environment(LOCAL_PAYMENT_SIMULATOR_ENABLED="true"),
        )

    def test_development_mailpit_and_sandbox_defaults_remain_usable(self):
        result = self.settings_process(
            "from TileCompany.settings import EMAIL_HOST, EMAIL_PORT, ZARINPAL_SANDBOX; "
            "assert (EMAIL_HOST, EMAIL_PORT, ZARINPAL_SANDBOX) == ('127.0.0.1', 1025, True)",
            APP_ENV="development",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_https_cookie_and_hsts_contract(self):
        result = self.settings_process(
            "from TileCompany import settings; "
            "assert settings.SECURE_SSL_REDIRECT is True; "
            "assert settings.SESSION_COOKIE_SECURE is True; "
            "assert settings.CSRF_COOKIE_SECURE is True; "
            "assert settings.SESSION_COOKIE_SAMESITE == settings.CSRF_COOKIE_SAMESITE == 'Lax'; "
            "assert settings.SECURE_HSTS_SECONDS == 3600; "
            "assert settings.SECURE_PROXY_SSL_HEADER is None",
            **self.production_environment(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_uses_enforced_csp_without_inline_script_execution(self):
        result = self.settings_process(
            "from TileCompany import settings; "
            "assert \"script-src 'unsafe-inline'\" not in settings.CONTENT_SECURITY_POLICY; "
            "assert 'unsafe-eval' not in settings.CONTENT_SECURITY_POLICY; "
            "assert \"object-src 'none'\" in settings.CONTENT_SECURITY_POLICY",
            **self.production_environment(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_logging_uses_the_redacting_console_handler(self):
        result = self.settings_process(
            "from TileCompany.settings import LOGGING; "
            "assert LOGGING['handlers']['console']['filters'] == ['redact_sensitive']; "
            "assert LOGGING['root']['level'] == 'WARNING'",
            **self.production_environment(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_csrf_origins_and_proxy_trust_are_explicit(self):
        configured = self.settings_process(
            "from TileCompany import settings; "
            "assert settings.CSRF_TRUSTED_ORIGINS == ['https://example.test']; "
            "assert settings.SECURE_PROXY_SSL_HEADER == ('HTTP_X_FORWARDED_PROTO', 'https')",
            **self.production_environment(
                CSRF_TRUSTED_ORIGINS="https://example.test",
                TRUST_PROXY_SSL_HEADER="true",
            ),
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assert_settings_failure(
            "without wildcards or paths",
            **self.production_environment(CSRF_TRUSTED_ORIGINS="https://*.example.test"),
        )
        self.assert_settings_failure(
            "must use https",
            **self.production_environment(CSRF_TRUSTED_ORIGINS="http://example.test"),
        )

    def test_development_uses_local_memory_cache(self):
        result = self.settings_process(
            "from TileCompany.settings import CACHES; "
            "assert CACHES['default']['BACKEND'].endswith('LocMemCache')",
            APP_ENV="development",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_redis_contract_and_local_cache_rejection(self):
        redis = self.settings_process(
            "from TileCompany.settings import CACHES; "
            "assert CACHES['default']['BACKEND'].endswith('RedisCache')",
            **self.production_environment(
                CACHE_BACKEND="redis",
                REDIS_URL="redis://:test-password@redis.example.test:6379/0",
            ),
        )
        self.assertEqual(redis.returncode, 0, redis.stderr)
        self.assert_settings_failure(
            "CACHE_BACKEND=redis requires the REDIS_URL",
            **self.production_environment(CACHE_BACKEND="redis", REDIS_URL=""),
        )
        self.assert_settings_failure(
            "requires CACHE_BACKEND=redis",
            **self.production_environment(CACHE_BACKEND="locmem", REDIS_URL=""),
        )

    def test_local_production_requires_hardened_database_cache_and_secret_contracts(self):
        environment = self.local_production_environment()
        probe = self.settings_process(
            "from TileCompany import settings; "
            "assert settings.DEBUG is False; "
            "assert settings.IS_HARDENED_RUNTIME is True; "
            "assert settings.SECURE_SSL_REDIRECT is True; "
            "assert settings.SESSION_COOKIE_SECURE is True; "
            "assert settings.CSRF_COOKIE_SECURE is True; "
            "assert settings.BROWSER_SECURITY_HEADERS_ENABLED is True; "
            "assert settings.CACHES['default']['BACKEND'].endswith('RedisCache')",
            **environment,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assert_settings_failure(
            "DJANGO_SECRET_KEY is required",
            **self.local_production_environment(DJANGO_SECRET_KEY=""),
        )
        self.assert_settings_failure(
            "APP_ENV=local_production requires DB_BACKEND=postgresql",
            **self.local_production_environment(DB_BACKEND="sqlite"),
        )
        self.assert_settings_failure(
            "CACHE_BACKEND=redis",
            **self.local_production_environment(CACHE_BACKEND="locmem", REDIS_URL=""),
        )

    def test_local_production_rejects_public_hosts_and_allows_only_local_hosts(self):
        self.assert_settings_failure(
            "explicit local verification hosts",
            **self.local_production_environment(ALLOWED_HOSTS="example.test"),
        )
        self.assert_settings_failure(
            "explicit local verification hosts",
            **self.local_production_environment(ALLOWED_HOSTS="*"),
        )

    def test_local_production_disables_external_payment_and_allows_explicit_local_simulator(self):
        probe = self.settings_process(
            "from TileCompany import settings; "
            "assert settings.EMAIL_BACKEND == 'TileCompany.email_backends.LocalProductionDisabledEmailBackend'; "
            "assert settings.ZARINPAL_MERCHANT_ID == ''; "
            "assert settings.ZARINPAL_SANDBOX is False; "
            "assert settings.ZARINPAL_DEVELOPMENT_AUTO_SUCCESS is False; "
            "assert settings.MOCK_PAYMENT_SIMULATION_ENABLED is False; "
            "assert settings.LOCAL_PAYMENT_SIMULATOR_ENABLED is True",
            **self.local_production_environment(
                EMAIL_HOST="smtp.example.test",
                ZARINPAL_MERCHANT_ID="00000000-0000-0000-0000-000000000001",
                ZARINPAL_SANDBOX="true",
                ZARINPAL_DEVELOPMENT_AUTO_SUCCESS="true",
                MOCK_PAYMENT_SIMULATION_ENABLED="true",
                LOCAL_PAYMENT_SIMULATOR_ENABLED="true",
            ),
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)

        email_attempt = self.settings_process(
            "import django; django.setup(); "
            "from django.core.mail import EmailMessage, get_connection; "
            "get_connection().send_messages([EmailMessage('probe', 'body', 'from@example.test', ['to@example.test'])])",
            **self.local_production_environment(),
        )
        self.assertNotEqual(email_attempt.returncode, 0)
        self.assertIn("Email delivery is disabled in APP_ENV=local_production", email_attempt.stderr)

        payment_attempt = self.settings_process(
            "import django; django.setup(); "
            "from types import SimpleNamespace; "
            "from orders.models import Order; "
            "from orders.payment import get_payment_gateway_for_order; "
            "from products.currency import Currency; "
            "get_payment_gateway_for_order(SimpleNamespace(payment_method=Order.PaymentMethod.ZARINPAL, currency=Currency.TOMAN))",
            **self.local_production_environment(),
        )
        self.assertNotEqual(payment_attempt.returncode, 0)
        self.assertIn("Online payment is temporarily unavailable", payment_attempt.stderr)

    def test_static_collection_root_is_separate_from_source_assets(self):
        result = self.settings_process(
            "from TileCompany import settings; "
            "assert settings.STATIC_ROOT != settings.BASE_DIR / 'static'; "
            "assert settings.STATIC_ROOT.name == 'staticfiles'",
            APP_ENV="development",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        blank_value = self.settings_process(
            "from TileCompany import settings; "
            "assert settings.STATIC_ROOT == settings.BASE_DIR / 'staticfiles'",
            APP_ENV="development",
            STATIC_ROOT="",
        )
        self.assertEqual(blank_value.returncode, 0, blank_value.stderr)

    def test_static_collection_root_can_be_deployment_configured(self):
        result = self.settings_process(
            "from TileCompany.settings import STATIC_ROOT; "
            "assert str(STATIC_ROOT) == r'C:\\deployment-static'",
            APP_ENV="development",
            STATIC_ROOT=r"C:\deployment-static",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
