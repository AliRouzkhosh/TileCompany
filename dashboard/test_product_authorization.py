from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from products.currency import Currency
from products.catalog_backfill import apply_tile_catalog
from products.catalog_compat import sync_tile_product_catalog
from products.models import Category, Product, ProductVariant, VariantPrice


class ProductAuthorizationTests(TestCase):
    def setUp(self):
        self.language = translation.override("en")
        self.language.__enter__()
        call_command("seed_dashboard_roles", verbosity=0)
        User = get_user_model()
        self.product_manager = User.objects.create_user("product-manager", password="secret")
        self.product_manager.groups.add(Group.objects.get(name="Product Manager"))
        self.narrow_editor = User.objects.create_user("variant-editor", password="secret")
        self.narrow_editor.user_permissions.add(
            Permission.objects.get(content_type__app_label="products", codename="change_productvariant")
        )
        self.legacy_staff = User.objects.create_user("legacy-staff", password="secret", is_staff=True)
        self.customer = User.objects.create_user("customer", password="secret")
        self.owner = User.objects.create_superuser("owner", "owner@example.com", "secret")
        apply_tile_catalog()
        self.product = Product.objects.create(name="Atlas", slug="atlas", sku="ATLAS")
        sync_tile_product_catalog(self.product)
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="ATLAS-01", stock=7, low_stock_threshold=3,
            sort_order=1, is_active=False,
        )
        VariantPrice.objects.create(
            variant=self.variant, currency=Currency.USD, amount=Decimal("100"), compare_at_amount=Decimal("120"),
        )
        VariantPrice.objects.create(
            variant=self.variant, currency=Currency.TOMAN, amount=Decimal("200000"), compare_at_amount=Decimal("250000"),
        )

    def tearDown(self):
        self.language.__exit__(None, None, None)

    def variant_post_data(self):
        return {
            "sku": self.variant.sku,
            "color": "", "size": "", "finish": "", "length_mm": "", "width_mm": "", "thickness_mm": "",
            "tiles_per_box": "", "coverage_per_box_m2": "", "price_unit": self.variant.price_unit,
            "stock": "999", "low_stock_threshold": "99", "sort_order": "9", "is_active": "",
            "usd_amount": "1", "usd_compare_at_amount": "2",
            "irt_amount": "1", "irt_compare_at_amount": "2",
        }

    def test_product_manager_non_staff_can_use_product_routes_but_not_schema(self):
        self.assertFalse(self.product_manager.is_staff)
        self.client.force_login(self.product_manager)
        self.assertEqual(self.client.get(reverse("dashboard:products:catalog")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:products:detail", args=[self.product.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:products:variants:catalog", args=[self.product.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:products:catalog_data")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:schema:home")).status_code, 403)

    def test_legacy_staff_and_customer_cannot_access_migrated_product_routes(self):
        catalog_url = reverse("dashboard:products:catalog")
        self.client.force_login(self.legacy_staff)
        self.assertEqual(self.client.get(catalog_url).status_code, 403)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(catalog_url).status_code, 403)
        self.client.logout()
        anonymous = self.client.get(catalog_url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse("accounts:login"), anonymous.url)

    def test_narrow_variant_editor_cannot_change_stock_prices_or_publication_by_crafted_post(self):
        self.client.force_login(self.narrow_editor)
        response = self.client.post(
            reverse("dashboard:products:variants:edit", args=[self.product.pk, self.variant.pk]),
            self.variant_post_data(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You do not have permission to change this field.")
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.sort_order, 1)
        self.assertEqual(self.variant.stock, 7)
        self.assertEqual(self.variant.low_stock_threshold, 3)
        self.assertFalse(self.variant.is_active)
        prices = {price.currency: price for price in self.variant.prices.all()}
        self.assertEqual(prices[Currency.USD].amount, Decimal("100"))
        self.assertEqual(prices[Currency.USD].compare_at_amount, Decimal("120"))
        self.assertEqual(prices[Currency.TOMAN].amount, Decimal("200000"))
        self.assertEqual(prices[Currency.TOMAN].compare_at_amount, Decimal("250000"))

    def test_product_manager_can_change_variant_inventory_and_pricing(self):
        self.client.force_login(self.product_manager)
        data = self.variant_post_data()
        data.update({"stock": "11", "low_stock_threshold": "4", "usd_amount": "110", "usd_compare_at_amount": "130"})
        response = self.client.post(reverse("dashboard:products:variants:edit", args=[self.product.pk, self.variant.pk]), data)
        self.assertEqual(response.status_code, 302)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 11)
        self.assertEqual(self.variant.low_stock_threshold, 4)
        self.assertEqual(self.variant.prices.get(currency=Currency.USD).amount, Decimal("110"))

    def test_catalog_taxonomy_requires_exact_model_permission(self):
        self.client.force_login(self.product_manager)
        create_url = reverse("dashboard:products:catalog_term_create", args=["categories"])
        response = self.client.post(create_url, {"name": "Wall", "name_fa": "", "slug": "wall"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Category.objects.filter(slug="wall").exists())

        self.client.force_login(self.legacy_staff)
        self.assertEqual(self.client.get(create_url).status_code, 403)

    def test_product_sidebar_is_permission_aware(self):
        self.client.force_login(self.product_manager)
        response = self.client.get(reverse("dashboard:home"))
        self.assertContains(response, "All Products")
        self.assertContains(response, "Catalog Data")
        self.assertNotContains(response, "Catalog Schema")
        self.assertNotContains(response, "Fulfillment")
        self.assertNotContains(response, "Needs Response")
        self.assertNotContains(response, "Analytics")

    def test_superuser_keeps_product_and_schema_authority(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("dashboard:products:catalog")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:schema:home")).status_code, 200)
