import io
import zipfile
from datetime import timedelta
from decimal import Decimal
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook

from products.currency import Currency
from products.inventory_price_imports import create_inventory_price_import_preview
from products.models import InventoryPriceImportBatch, Product, ProductVariant, VariantPrice
from products.storage import private_inventory_import_storage


HEADERS = "sku,stock,low_stock_threshold,irt_price,irt_compare_at,usd_price,usd_compare_at\n"


class InventoryPriceImportPreviewTests(TestCase):
    def setUp(self):
        self.private_storage_directory = TemporaryDirectory()
        self.previous_private_storage_location = private_inventory_import_storage.location
        private_inventory_import_storage.location = self.private_storage_directory.name
        self.addCleanup(self._restore_private_storage)
        call_command("seed_dashboard_roles", verbosity=0)
        User = get_user_model()
        self.manager = User.objects.create_user("product-manager", password="secret")
        self.manager.groups.add(Group.objects.get(name="Product Manager"))
        self.run_only = User.objects.create_user("run-only", password="secret")
        self.run_only.user_permissions.add(Permission.objects.get(content_type__app_label="products", codename="run_inventory_price_import"))
        self.customer = User.objects.create_user("customer", password="secret")
        self.product = Product.objects.create(name="Import tile", slug="import-tile", sku="IMPORT-TILE")
        self.variant = ProductVariant.objects.create(product=self.product, sku="import-01", stock=5, low_stock_threshold=2)
        VariantPrice.objects.create(variant=self.variant, currency=Currency.TOMAN, amount=Decimal("100"), compare_at_amount=Decimal("120"))
        VariantPrice.objects.create(variant=self.variant, currency=Currency.USD, amount=Decimal("10.00"), compare_at_amount=Decimal("12.00"))

    def _restore_private_storage(self):
        private_inventory_import_storage.location = self.previous_private_storage_location
        self.private_storage_directory.cleanup()

    def upload(self, body, name="inventory.csv"):
        return SimpleUploadedFile(name, body if isinstance(body, bytes) else body.encode("utf-8"), content_type="text/csv")

    def preview(self, body, *, actor=None, name="inventory.csv"):
        return create_inventory_price_import_preview(actor=actor or self.manager, uploaded_file=self.upload(body, name))

    def test_preview_persists_authoritative_changed_snapshot_without_commerce_mutation(self):
        batch = self.preview(HEADERS + " IMPORT-01 ,0,3,110,CLEAR,11.50,13\n")
        row = batch.rows.get()
        self.assertEqual(batch.status, InventoryPriceImportBatch.Status.VALIDATED)
        self.assertEqual(batch.changed_rows, 1)
        self.assertEqual(row.sku_snapshot, "IMPORT-01")
        self.assertEqual(row.normalized_patch["stock"], 0)
        self.assertEqual(row.proposed_after_snapshot["IRT"]["compare_at_amount"], None)
        self.variant.refresh_from_db()
        self.assertEqual((self.variant.stock, self.variant.low_stock_threshold), (5, 2))
        self.assertEqual(self.variant.prices.get(currency=Currency.TOMAN).amount, Decimal("100"))
        self.assertEqual(batch.source_sha256, __import__("hashlib").sha256((HEADERS + " IMPORT-01 ,0,3,110,CLEAR,11.50,13\n").encode()).hexdigest())
        delta = batch.source_expires_at - batch.created_at
        self.assertAlmostEqual(
            delta.total_seconds(),
            timedelta(days=90).total_seconds(),
            delta=1,
        )

    def test_blank_is_unchanged_zero_stock_is_valid_and_zero_price_is_error(self):
        valid = self.preview(HEADERS + "IMPORT-01,0,,,,,,\n")
        self.assertEqual(valid.status, InventoryPriceImportBatch.Status.VALIDATED)
        self.assertEqual(valid.rows.get().changed_fields, ["stock"])
        invalid = self.preview(HEADERS + "IMPORT-01,,,0,,,\n")
        self.assertEqual(invalid.status, InventoryPriceImportBatch.Status.INVALID)
        self.assertEqual(invalid.rows.get().errors[0]["code"], "price_zero")

    def test_compare_at_uses_current_or_proposed_selling_price(self):
        valid = self.preview(HEADERS + "IMPORT-01,,,,130,,,\n")
        self.assertEqual(valid.status, InventoryPriceImportBatch.Status.VALIDATED)
        invalid = self.preview(HEADERS + "IMPORT-01,,,130,,,\n")
        self.assertEqual(invalid.status, InventoryPriceImportBatch.Status.INVALID)
        self.assertEqual(invalid.rows.get().errors[0]["code"], "compare_at_not_above_price")
        self.variant.prices.filter(currency=Currency.USD).delete()
        missing = self.preview(HEADERS + "IMPORT-01,,,,, ,150\n")
        self.assertEqual(missing.status, InventoryPriceImportBatch.Status.INVALID)
        self.assertEqual(missing.rows.get().errors[0]["code"], "compare_at_requires_price")

    def test_duplicate_unknown_and_blank_skus_are_row_errors(self):
        batch = self.preview(HEADERS + "IMPORT-01,1,,,,,,\nimport-01,2,,,,,,\nUNKNOWN,3,,,,,,\n,4,,,,,,\n")
        self.assertEqual(batch.status, InventoryPriceImportBatch.Status.INVALID)
        self.assertEqual(batch.error_rows, 4)
        self.assertEqual({row.errors[0]["code"] for row in batch.rows.all()}, {"duplicate_sku", "unknown_sku", "blank_sku"})

    def test_run_permission_is_sufficient_and_other_user_is_denied(self):
        self.assertTrue(self.run_only.has_perm("products.run_inventory_price_import"))
        self.assertFalse(self.run_only.has_perm("products.apply_inventory_price_import"))
        self.preview(HEADERS + "IMPORT-01,1,,,,,,\n", actor=self.run_only)
        with self.assertRaises(PermissionDenied):
            self.preview(HEADERS + "IMPORT-01,1,,,,,,\n", actor=self.customer)

    def test_csv_header_and_numeric_security_contract(self):
        for body in (
            "sku;stock;low_stock_threshold;irt_price;irt_compare_at;usd_price;usd_compare_at\nIMPORT-01;1;;;;;\n",
            "sku,sku,stock,low_stock_threshold,irt_price,irt_compare_at,usd_price,usd_compare_at\nIMPORT-01,IMPORT-01,1,,,,,\n",
            HEADERS + "IMPORT-01,-1,,,,,,\n",
            HEADERS + "IMPORT-01,۱,,,,,,\n",
            HEADERS + "IMPORT-01,, ,,,10.123,\n",
        ):
            batch = self.preview(body)
            self.assertEqual(batch.status, InventoryPriceImportBatch.Status.INVALID)
        bom = self.preview(("\ufeff" + HEADERS + "IMPORT-01,1,,,,,,\n").encode())
        self.assertEqual(bom.status, InventoryPriceImportBatch.Status.VALIDATED)

    def test_xlsx_data_sheet_and_formula_contract(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Data"
        sheet.append(HEADERS.strip().split(","))
        sheet.append(["IMPORT-01", 1, None, None, None, None, None])
        output = io.BytesIO()
        workbook.save(output)
        batch = self.preview(output.getvalue(), name="inventory.xlsx")
        self.assertEqual(batch.status, InventoryPriceImportBatch.Status.VALIDATED)

        sheet["B2"] = "=1+1"
        output = io.BytesIO()
        workbook.save(output)
        formula = self.preview(output.getvalue(), name="inventory.xlsx")
        self.assertEqual(formula.status, InventoryPriceImportBatch.Status.INVALID)
        self.assertEqual(formula.failure_summary["errors"][0]["code"], "formula_not_allowed")

    def test_xlsx_archive_member_limit_is_enforced_before_workbook_parsing(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            for index in range(101):
                archive.writestr(f"member-{index}.txt", b"x")
        batch = self.preview(payload.getvalue(), name="inventory.xlsx")
        self.assertEqual(batch.status, InventoryPriceImportBatch.Status.INVALID)
        self.assertEqual(batch.failure_summary["errors"][0]["code"], "xlsx_archive_size")

    def test_role_counts_and_source_format_mismatch(self):
        self.assertEqual(Group.objects.get(name="Product Manager").permissions.count(), 59)
        self.assertEqual(Group.objects.get(name="Order Manager").permissions.count(), 5)
        with self.assertRaises(Exception):
            self.preview(HEADERS.encode(), name="inventory.xlsx")

    def test_private_source_storage_has_no_public_url(self):
        batch = self.preview(HEADERS + "IMPORT-01,1,,,,,,\n")
        self.assertTrue(batch.source_file.storage.exists(batch.source_file.name))
        with self.assertRaises(ValueError):
            batch.source_file.storage.url(batch.source_file.name)
