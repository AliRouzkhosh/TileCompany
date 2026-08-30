import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class LocalProductionMediaContractTests(SimpleTestCase):
    """Guard the explicit public-media boundary in the local-production proxy."""

    def setUp(self):
        root = Path(settings.BASE_DIR)
        self.nginx = (root / "ops" / "local-prod" / "nginx" / "nginx.conf").read_text(
            encoding="utf-8"
        )
        self.compose = (root / "ops" / "local-prod" / "compose.yml").read_text(
            encoding="utf-8"
        )

    def test_only_audited_public_prefixes_are_directly_served(self):
        for prefix in (
            "products",
            "storefront/homepage",
            "storefront/categories",
            "storefront/discovery",
            "blog",
        ):
            with self.subTest(prefix=prefix):
                self.assertIn(f"location /media/{prefix}/", self.nginx)
                self.assertIn(f"alias /var/www/media/{prefix}/", self.nginx)

        self.assertNotIn("location /media/ {", self.nginx)
        self.assertNotIn("location /media/support/", self.nginx)
        self.assertNotIn("location /media/orders/refunds/", self.nginx)

    def test_media_volume_is_initialized_for_nonroot_web_and_read_only_in_nginx(self):
        self.assertIn("media_data:/app/media", self.compose)
        self.assertIn("media_data:/var/www/media:ro", self.compose)
        self.assertIn('user: "0:0"', self.compose)
        self.assertIn("chown 10001:10001 /app/media", self.compose)
        self.assertIn('network_mode: "none"', self.compose)
        self.assertIn("condition: service_completed_successfully", self.compose)

    def test_proxy_config_keeps_logs_private_and_rejects_unknown_tls_hosts(self):
        self.assertIn("map $uri $privacy_safe_path", self.nginx)
        self.assertIn('$request_method $privacy_safe_path $server_protocol', self.nginx)
        self.assertNotIn('"$request" $status', self.nginx)
        self.assertIn('/accounts/reset/[redacted]/[redacted]/', self.nginx)
        self.assertIn('/accounts/login/sms/[redacted]/', self.nginx)
        self.assertIn('/accounts/register/sms/[redacted]/', self.nginx)
        self.assertIn('/accounts/profile/phone/[redacted]/', self.nginx)
        self.assertIn("listen 443 ssl default_server", self.nginx)
        self.assertIn("server_name _;", self.nginx)
        self.assertIn("return 444;", self.nginx)

    def test_sensitive_account_log_paths_are_precise(self):
        """Keep UUID challenge routes redacted without hiding static siblings."""
        map_block = re.search(
            r"map \$uri \$privacy_safe_path \{(?P<body>.*?)^    \}",
            self.nginx,
            flags=re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(map_block)
        rules = []
        for line in map_block.group("body").splitlines():
            parts = line.strip().split()
            if parts and parts[0].strip('"').startswith("~"):
                rules.append((parts[0].strip('"')[1:], parts[1].rstrip(";")))

        def privacy_safe_path(path):
            for pattern, replacement in rules:
                if re.match(pattern, path):
                    return replacement
            return path

        challenge_id = "11111111-1111-4111-8111-111111111111"
        for locale in ("en", "fa"):
            with self.subTest(locale=locale):
                self.assertEqual(
                    privacy_safe_path(f"/{locale}/accounts/register/sms/complete/"),
                    f"/{locale}/accounts/register/sms/complete/",
                )
                self.assertEqual(
                    privacy_safe_path(f"/{locale}/accounts/register/sms/{challenge_id}/"),
                    "/[locale]/accounts/register/sms/[redacted]/",
                )
                self.assertEqual(
                    privacy_safe_path(f"/{locale}/accounts/register/sms/{challenge_id}/resend/"),
                    "/[locale]/accounts/register/sms/[redacted]/resend/",
                )
                self.assertEqual(
                    privacy_safe_path(f"/{locale}/accounts/login/sms/{challenge_id}/"),
                    "/[locale]/accounts/login/sms/[redacted]/",
                )
                self.assertEqual(
                    privacy_safe_path(f"/{locale}/accounts/login/sms/{challenge_id}/resend/"),
                    "/[locale]/accounts/login/sms/[redacted]/resend/",
                )
                self.assertEqual(
                    privacy_safe_path(f"/{locale}/accounts/profile/phone/{challenge_id}/"),
                    "/[locale]/accounts/profile/phone/[redacted]/",
                )
                self.assertEqual(
                    privacy_safe_path(f"/{locale}/accounts/profile/phone/{challenge_id}/resend/"),
                    "/[locale]/accounts/profile/phone/[redacted]/resend/",
                )
                self.assertEqual(
                    privacy_safe_path(f"/{locale}/accounts/reset/syntheticuid/synthetictoken/"),
                    "/[locale]/accounts/reset/[redacted]/[redacted]/",
                )
                self.assertEqual(
                    privacy_safe_path(f"/{locale}/accounts/login/sms/not-a-uuid/"),
                    f"/{locale}/accounts/login/sms/not-a-uuid/",
                )
                for path in (
                    f"/{locale}/accounts/login/sms/",
                    f"/{locale}/accounts/register/sms/",
                    f"/{locale}/accounts/profile/phone/",
                ):
                    self.assertEqual(privacy_safe_path(path), path)

        self.assertNotIn("/accounts/register/sms/[^/]+/$", self.nginx)
        self.assertNotIn("/accounts/login/sms/[^/]+/$", self.nginx)
        self.assertNotIn("/accounts/profile/phone/[^/]+/$", self.nginx)

    def test_direct_static_files_keep_cache_policy_and_disable_mime_sniffing(self):
        self.assertIn("location /static/", self.nginx)
        self.assertIn("expires 1h;", self.nginx)
        self.assertIn('add_header X-Content-Type-Options "nosniff" always;', self.nginx)
