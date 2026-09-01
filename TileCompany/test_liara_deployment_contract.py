"""Provider-neutral contract tests for the Liara staging web artifact."""

import json
import shutil
import subprocess
from pathlib import Path

from django.test import SimpleTestCase


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LiaraDeploymentContractTests(SimpleTestCase):
    def setUp(self):
        self.dockerfile = (PROJECT_ROOT / "ops" / "liara" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        self.start_script = PROJECT_ROOT / "ops" / "liara" / "start-web.sh"
        self.startup = self.start_script.read_text(encoding="utf-8")
        self.manifest = json.loads((PROJECT_ROOT / "liara.json").read_text(encoding="utf-8"))
        self.local_compose = (PROJECT_ROOT / "ops" / "local-prod" / "compose.yml").read_text(
            encoding="utf-8"
        )

    def test_liara_manifest_is_minimal_and_provider_neutral(self):
        self.assertEqual(self.manifest, {
            "port": 8000,
            "build": {"dockerfile": "ops/liara/Dockerfile"},
        })
        rendered = json.dumps(self.manifest).lower()
        for forbidden in ("secret", "password", "redis://", "postgres", "liara.run"):
            self.assertNotIn(forbidden, rendered)

    def test_startup_script_is_valid_posix_shell(self):
        if shutil.which("sh") is None:
            # Windows source-test hosts need the Docker runtime validation below;
            # preserve a minimal cross-platform source assertion here.
            self.assertTrue(self.startup.startswith("#!/bin/sh\n"))
            return
        result = subprocess.run(
            ["sh", "-n", str(self.start_script)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_startup_preserves_the_gunicorn_contract(self):
        self.assertIn("set -eu", self.startup)
        self.assertIn("exec gunicorn", self.startup)
        self.assertIn('--bind "0.0.0.0:${PORT:-8000}"', self.startup)
        self.assertIn("--workers 2", self.startup)
        self.assertIn("--timeout 60", self.startup)
        self.assertIn("--error-logfile=-", self.startup)
        self.assertIn("--capture-output", self.startup)
        self.assertNotIn("--access-logfile", self.startup)
        self.assertNotIn("manage.py migrate", self.startup)
        self.assertNotIn("collectstatic", self.startup)
        self.assertIn("TileCompany.wsgi:application", self.startup)

    def test_image_contract_remains_nonroot_and_matches_the_frozen_runtime(self):
        self.assertIn("FROM python:3.12.12-slim-bookworm", self.dockerfile)
        self.assertIn("WORKDIR /app", self.dockerfile)
        self.assertIn("python -m pip check", self.dockerfile)
        self.assertIn("--uid 10001", self.dockerfile)
        self.assertIn("--gid 10001", self.dockerfile)
        self.assertIn("USER tilecompany", self.dockerfile)
        self.assertIn("/app/ops/liara/start-web.sh", self.dockerfile)
        self.assertIn('CMD ["/app/ops/liara/start-web.sh"]', self.dockerfile)
        self.assertNotIn("USER root", self.dockerfile)

    def test_persistent_mount_paths_are_explicit_without_public_private_storage(self):
        self.assertIn("/app/media", self.dockerfile)
        self.assertIn("/app/private_inventory_price_import_sources", self.dockerfile)
        self.assertNotIn("MEDIA_URL", self.dockerfile)
        self.assertNotIn("private_inventory_import_data", self.dockerfile)

    def test_local_production_compose_contract_is_unchanged(self):
        web_block = self.local_compose.split("\n  web:\n", 1)[1].split("\n  media-init:", 1)[0]
        self.assertIn("--bind=0.0.0.0:8000", web_block)
        self.assertIn("--workers=2", web_block)
        self.assertIn("--timeout=60", web_block)
        self.assertIn("--error-logfile=-", web_block)
        self.assertIn("--capture-output", web_block)
        self.assertNotIn("--access-logfile", web_block)
