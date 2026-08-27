from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.core.exceptions import PermissionDenied
from unittest.mock import patch

from products.currency import Currency
from products.inventory_price_imports import apply_inventory_price_import, create_inventory_price_import_preview
from products.models import InventoryPriceImportBatch, Product, ProductOperationalChange, ProductVariant, VariantPrice
from dashboard.notifications import get_inventory_alerts
from django.urls import reverse


HEADERS = b"sku,stock,low_stock_threshold,irt_price,irt_compare_at,usd_price,usd_compare_at\n"


class InventoryPriceImportApplyTests(TestCase):
    def setUp(self):
        call_command("seed_dashboard_roles", verbosity=0)
        User = get_user_model()
        self.actor = User.objects.create_user("manager", password="secret")
        self.actor.groups.add(Group.objects.get(name="Product Manager"))
        product = Product.objects.create(name="Apply tile", slug="apply-tile", sku="APPLY")
        self.variant = ProductVariant.objects.create(product=product, sku="APPLY-1", stock=5, low_stock_threshold=2)
        VariantPrice.objects.create(variant=self.variant, currency=Currency.TOMAN, amount=Decimal("100"), compare_at_amount=Decimal("120"))

    def preview(self, line):
        return create_inventory_price_import_preview(actor=self.actor, uploaded_file=SimpleUploadedFile("apply.csv", HEADERS + line))

    def test_apply_is_atomic_idempotent_and_audited(self):
        batch = self.preview(b"APPLY-1,7,3,110,CLEAR,10,12\n")
        result = apply_inventory_price_import(actor=self.actor, batch_id=batch.pk)
        self.assertEqual(result["status"], InventoryPriceImportBatch.Status.APPLIED)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.stock, self.variant.low_stock_threshold), (7, 3))
        self.assertEqual(self.variant.prices.get(currency=Currency.TOMAN).compare_at_amount, None)
        self.assertEqual(ProductOperationalChange.objects.count(), 1)
        self.assertTrue(apply_inventory_price_import(actor=self.actor, batch_id=batch.pk)["already_applied"])
        self.assertEqual(ProductOperationalChange.objects.count(), 1)

    def test_field_aware_stale_does_not_overwrite_stock_for_price_only_batch(self):
        batch = self.preview(b"APPLY-1,,,110,,,\n")
        self.variant.stock = 4; self.variant.save(update_fields=["stock"])
        result = apply_inventory_price_import(actor=self.actor, batch_id=batch.pk)
        self.assertEqual(result["status"], InventoryPriceImportBatch.Status.APPLIED)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 4)
        self.assertEqual(self.variant.prices.get(currency=Currency.TOMAN).amount, Decimal("110"))

    def test_stock_change_stales_without_partial_write(self):
        batch = self.preview(b"APPLY-1,7,,,,,,\n")
        self.variant.stock = 4; self.variant.save(update_fields=["stock"])
        result = apply_inventory_price_import(actor=self.actor, batch_id=batch.pk)
        self.assertEqual(result["status"], InventoryPriceImportBatch.Status.STALE)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.stock, 4)
        self.assertEqual(ProductOperationalChange.objects.count(), 0)

    def test_missing_price_clear_zero_change_and_tamper_are_safe(self):
        self.variant.prices.filter(currency=Currency.USD).delete()
        created = self.preview(b"APPLY-1,,,,,10,12\n")
        apply_inventory_price_import(actor=self.actor, batch_id=created.pk)
        self.assertEqual(self.variant.prices.filter(currency=Currency.USD).count(), 1)
        audit = ProductOperationalChange.objects.latest("pk")
        self.assertFalse(audit.before_snapshot["USD"]["exists"]); self.assertTrue(audit.after_snapshot["USD"]["exists"])
        no_op = self.preview(b"APPLY-1,,,,,,\n")
        result = apply_inventory_price_import(actor=self.actor, batch_id=no_op.pk)
        self.assertTrue(result["no_changes"]); self.assertEqual(ProductOperationalChange.objects.count(), 1)
        tampered = self.preview(b"APPLY-1,7,,,,,,\n")
        row = tampered.rows.get(); row.changed_fields = ["is_active"]; row.save(update_fields=["changed_fields"])
        with self.assertRaises(ValueError): apply_inventory_price_import(actor=self.actor, batch_id=tampered.pk)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.stock, 5)

    def test_authorization_multirow_and_price_dependency_stale(self):
        User = get_user_model()
        for role in ("Order Manager", "Support Manager", "Content Manager", "Moderator", "Analyst"):
            user = User.objects.create_user(role); user.groups.add(Group.objects.get(name=role))
            batch = self.preview(b"APPLY-1,7,,,,,,\n")
            with self.assertRaises(PermissionDenied): apply_inventory_price_import(actor=user, batch_id=batch.pk)
        dependent = self.preview(b"APPLY-1,,,110,,,\n")
        price = self.variant.prices.get(currency=Currency.TOMAN); price.compare_at_amount = Decimal("130"); price.save()
        self.assertEqual(apply_inventory_price_import(actor=self.actor, batch_id=dependent.pk)["status"], InventoryPriceImportBatch.Status.STALE)
        other = ProductVariant.objects.create(product=self.variant.product, sku="APPLY-2", stock=8)
        multi = self.preview(b"APPLY-1,10,,,,,,\nAPPLY-2,12,,,,,,\n")
        other.stock = 7; other.save(update_fields=["stock"])
        self.assertEqual(apply_inventory_price_import(actor=self.actor, batch_id=multi.pk)["status"], InventoryPriceImportBatch.Status.STALE)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.stock, 5)

    def test_owner_run_only_legacy_staff_and_customer_apply_boundaries(self):
        User = get_user_model()
        owner = User.objects.create_superuser("owner", "owner@example.test", "secret")
        owner_batch = self.preview(b"APPLY-1,7,,,,,,\n")
        self.assertEqual(apply_inventory_price_import(actor=owner, batch_id=owner_batch.pk)["status"], InventoryPriceImportBatch.Status.APPLIED)

        run_only = User.objects.create_user("run-only")
        run_only.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="products", codename="run_inventory_price_import"
            )
        )
        legacy_staff = User.objects.create_user("legacy", is_staff=True)
        customer = User.objects.create_user("customer")
        for user in (run_only, legacy_staff, customer):
            with self.assertRaises(PermissionDenied):
                apply_inventory_price_import(actor=user, batch_id=owner_batch.pk)

    def test_stale_restock_and_stock_only_does_not_clobber_price(self):
        restock = self.preview(b"APPLY-1,15,,,,,,\n")
        self.variant.stock = 11; self.variant.save(update_fields=["stock"])
        self.assertEqual(apply_inventory_price_import(actor=self.actor, batch_id=restock.pk)["status"], InventoryPriceImportBatch.Status.STALE)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.stock, 11)

        stock_only = self.preview(b"APPLY-1,16,,,,,,\n")
        price = self.variant.prices.get(currency=Currency.TOMAN)
        price.amount = Decimal("105"); price.compare_at_amount = Decimal("130"); price.save()
        self.assertEqual(apply_inventory_price_import(actor=self.actor, batch_id=stock_only.pk)["status"], InventoryPriceImportBatch.Status.APPLIED)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.stock, 16)
        self.assertEqual(self.variant.prices.get(currency=Currency.TOMAN).amount, Decimal("105"))

    def test_corrupt_changed_fields_and_invalid_value_fail_before_commerce_write(self):
        batch = self.preview(b"APPLY-1,7,,,,,,\n")
        row = batch.rows.get()
        row.changed_fields = []
        row.save(update_fields=["changed_fields"])
        with self.assertRaises(ValueError):
            apply_inventory_price_import(actor=self.actor, batch_id=batch.pk)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.stock, 5)
        batch.refresh_from_db(); self.assertEqual(batch.status, InventoryPriceImportBatch.Status.FAILED)

        invalid = self.preview(b"APPLY-1,7,,,,,,\n")
        row = invalid.rows.get()
        row.proposed_after_snapshot["stock"] = -1
        row.save(update_fields=["proposed_after_snapshot"])
        with self.assertRaises(ValueError):
            apply_inventory_price_import(actor=self.actor, batch_id=invalid.pk)
        self.variant.refresh_from_db(); self.assertEqual(self.variant.stock, 5)

    def test_commerce_and_audit_failures_roll_back_and_mark_batch_failed(self):
        other = ProductVariant.objects.create(product=self.variant.product, sku="APPLY-2", stock=8)
        batch = self.preview(b"APPLY-1,10,,,,,,\nAPPLY-2,12,,,,,,\n")
        with patch("products.inventory_price_imports._audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                apply_inventory_price_import(actor=self.actor, batch_id=batch.pk)
        self.variant.refresh_from_db(); other.refresh_from_db(); batch.refresh_from_db()
        self.assertEqual((self.variant.stock, other.stock), (5, 8))
        self.assertEqual(ProductOperationalChange.objects.count(), 0)
        self.assertEqual(batch.status, InventoryPriceImportBatch.Status.FAILED)

    def test_inactive_variant_is_corrected_without_publication_side_effect(self):
        self.variant.is_active = False; self.variant.save(update_fields=["is_active"])
        batch = self.preview(b"APPLY-1,0,,,,,,\n")
        self.assertEqual(apply_inventory_price_import(actor=self.actor, batch_id=batch.pk)["status"], InventoryPriceImportBatch.Status.APPLIED)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 0)
        self.assertFalse(self.variant.is_active)

    def test_successful_import_updates_the_existing_inventory_alert_projection(self):
        self.variant.is_active = True; self.variant.save(update_fields=["is_active"])
        batch = self.preview(b"APPLY-1,2,,,,,,\n")
        apply_inventory_price_import(actor=self.actor, batch_id=batch.pk)
        alerts = get_inventory_alerts(self.actor)
        self.assertEqual((alerts["low_stock_count"], alerts["out_of_stock_count"]), (1, 0))
        self.assertEqual(alerts["items"][0]["url"], reverse("dashboard:products:variants:edit", args=[self.variant.product_id, self.variant.pk]))

    def test_hundred_stock_changes_lock_and_resolve_variants_in_sets(self):
        variants = [
            ProductVariant.objects.create(product=self.variant.product, sku=f"BULK-{index:03d}", stock=1)
            for index in range(100)
        ]
        payload = b"".join(f"{variant.sku},2,,,,,,\n".encode() for variant in variants)
        batch = self.preview(payload)
        with CaptureQueriesContext(connection) as queries:
            result = apply_inventory_price_import(actor=self.actor, batch_id=batch.pk)
        self.assertEqual(result["changed_variants"], 100)
        # Apply intentionally has per-Variant writes/audit records, but SKU resolution
        # and deterministic Variant locking must remain set based rather than N+1.
        # 608 queries on SQLite: the expected per-row validated write, refresh, and
        # audit sequence, plus set-based staging/Variant discovery.  Keep a ceiling
        # that detects a new per-SKU lookup without replacing safe price writes with
        # an unsafe bulk update.
        self.assertLess(len(queries), 650)
