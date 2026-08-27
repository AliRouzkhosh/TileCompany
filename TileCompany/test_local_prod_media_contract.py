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
