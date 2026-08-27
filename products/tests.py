from decimal import Decimal
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from products.models import (
    Brand,
    Category,
    Color,
    Finish,
    Material,
    Product,
    ProductType,
    ProductVariant,
    Size,
    Usage,
    VariantPrice,
    Wishlist,
)
from products.search.engine import search_products
from products.currency import Currency
from products.selectors import (
    annotate_product_price_range,
    get_filtered_products,
    get_product_price_range,
    get_related_products,
    get_sorted_products,
    attach_target_variant_ids,
    get_variant_price,
)
from products.services import set_variant_price


class PriceSyncCommandTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name="Sync tile", slug="sync-tile", sku="SYNC-TILE")
        self.variant = ProductVariant.objects.create(product=self.product, sku="SYNC-TILE-01")

    def test_sync_prices_requires_an_explicit_currency(self):
        with self.assertRaises(CommandError):
            call_command("sync_prices", "missing.xlsx")

    def test_sync_prices_updates_only_the_requested_currency(self):
        import openpyxl

        VariantPrice.objects.create(variant=self.variant, currency=Currency.TOMAN, amount=Decimal("2500000"))
        workbook_file = NamedTemporaryFile(suffix=".xlsx", delete=False)
        workbook_file.close()
        try:
            workbook = openpyxl.Workbook()
            worksheet = workbook.active
            worksheet.append(["SKU", "Amount", "Compare At Amount"])
            worksheet.append([self.variant.sku, "32.50", "40.00"])
            workbook.save(workbook_file.name)
            call_command("sync_prices", workbook_file.name, "--currency", Currency.USD)
        finally:
            os.unlink(workbook_file.name)

        self.assertEqual(VariantPrice.objects.get(variant=self.variant, currency=Currency.USD).amount, Decimal("32.50"))
        self.assertEqual(VariantPrice.objects.get(variant=self.variant, currency=Currency.TOMAN).amount, Decimal("2500000.00"))


class ProductCategoryTests(TestCase):
    def test_legacy_catalog_price_fields_are_removed(self):
        product_fields = {field.name for field in Product._meta.get_fields()}
        variant_fields = {field.name for field in ProductVariant._meta.get_fields()}

        self.assertFalse({"price", "original_price", "price_unit", "price_updated_at"} & product_fields)
        self.assertFalse({"price", "original_price"} & variant_fields)
        self.assertIn("price_unit", variant_fields)

    def setUp(self):
        self.floor = Category.objects.create(
            name="Floor Tiles",
            slug="floor-tiles",
        )
        self.wall = Category.objects.create(
            name="Wall Tiles",
            slug="wall-tiles",
        )
        self.outdoor = Category.objects.create(
            name="Outdoor Tiles",
            slug="outdoor-tiles",
        )
        self.porcelain = Material.objects.create(
            name="Porcelain",
            slug="porcelain",
        )
        self.floor_usage = Usage.objects.create(
            name="Floor",
            slug="floor",
        )
        self.indoor_usage = Usage.objects.create(
            name="Indoor",
            slug="indoor",
        )
        self.white = Color.objects.create(
            name="White",
            slug="white",
        )
        self.matte = Finish.objects.create(
            name="Matte",
            slug="matte",
        )
        self.size = Size.objects.create(
            name="60x60cm",
            slug="60x60cm",
            length_mm=600,
            width_mm=600,
        )

        self.product = Product.objects.create(
            name="Atlas Stone",
            slug="atlas-stone",
            sku="ATLAS-STONE",
            is_active=True,
        )
        self.product.categories.set([self.floor, self.wall])
        self.product.materials.set([self.porcelain])
        self.product.usages.set([self.floor_usage, self.indoor_usage])
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="ATLAS-STONE-WHITE",
            color=self.white,
            size=self.size,
            finish=self.matte,
            stock=10,
        )
        VariantPrice.objects.create(
            variant=self.variant,
            currency=Currency.USD,
            amount=Decimal("100.00"),
        )

        self.related = Product.objects.create(
            name="Atlas Outdoor",
            slug="atlas-outdoor",
            sku="ATLAS-OUTDOOR",
            is_active=True,
        )
        self.related.categories.set([self.wall, self.outdoor])

        self.unrelated = Product.objects.create(
            name="Standalone Mosaic",
            slug="standalone-mosaic",
            sku="STANDALONE-MOSAIC",
            is_active=True,
        )
        self.unrelated.categories.set([self.outdoor])

    def test_product_can_belong_to_multiple_categories(self):
        self.assertEqual(self.product.categories.count(), 2)

    def test_product_can_have_multiple_controlled_usages(self):
        self.assertEqual(self.product.usages.count(), 2)

    def test_product_can_have_controlled_materials(self):
        self.assertEqual(list(self.product.materials.all()), [self.porcelain])

    def test_variant_uses_controlled_attributes(self):
        self.assertEqual(self.variant.color, self.white)
        self.assertEqual(self.variant.size, self.size)
        self.assertEqual(self.variant.finish, self.matte)

    def test_product_availability_is_derived_from_active_variants(self):
        self.assertEqual(self.product.total_stock, 10)
        self.assertTrue(self.product.in_stock)

        self.variant.is_active = False
        self.variant.save(update_fields=["is_active"])
        self.assertEqual(self.product.total_stock, 0)
        self.assertFalse(self.product.in_stock)

    def test_variant_low_stock_states_ignore_inactive_variants(self):
        self.variant.stock = 3
        self.variant.low_stock_threshold = 5
        self.variant.save(update_fields=["stock", "low_stock_threshold"])
        self.assertTrue(self.variant.is_low_stock)
        self.assertFalse(self.variant.is_out_of_stock)

        self.variant.stock = 0
        self.variant.save(update_fields=["stock"])
        self.assertTrue(self.variant.is_out_of_stock)

        self.variant.is_active = False
        self.variant.save(update_fields=["is_active"])
        self.assertFalse(self.variant.is_out_of_stock)

    def test_catalog_filter_matches_any_selected_category_once(self):
        products, _ = get_filtered_products(
            selected_categories=["floor-tiles", "wall-tiles"],
            currency=Currency.USD,
        )

        self.assertIn(self.product, products)
        self.assertEqual(
            products.filter(pk=self.product.pk).count(),
            1,
        )

    def test_related_products_share_any_category(self):
        related_products = get_related_products(self.product, Currency.USD)

        self.assertIn(self.related, related_products)
        self.assertNotIn(self.unrelated, related_products)

    def test_search_matches_category_name(self):
        results = search_products("Floor")

        self.assertIn(self.product, results)

    def test_search_matches_controlled_variant_attribute(self):
        results = search_products("White")

        self.assertIn(self.product, results)

    def test_persian_catalog_and_detail_prefer_localized_product_data(self):
        self.floor.name_fa = "کاشی کف"
        self.floor.save(update_fields=["name_fa"])
        self.porcelain.name_fa = "پرسلان"
        self.porcelain.save(update_fields=["name_fa"])
        self.white.name_fa = "سفید"
        self.white.save(update_fields=["name_fa"])
        self.product.name_fa = "سنگ اطلس"
        self.product.description = "English description"
        self.product.description_fa = "توضیحات فارسی"
        self.product.save(update_fields=["name_fa", "description", "description_fa"])

        catalog = self.client.get("/fa/shop/")
        category = self.client.get("/fa/shop/category/floor-tiles/")
        detail = self.client.get(f"/fa/product/{self.product.slug}/")
        english = self.client.get(f"/en/product/{self.product.slug}/")

        for response in (catalog, category, detail, english):
            self.assertEqual(response.status_code, 200)
        self.assertContains(catalog, "سنگ اطلس")
        self.assertContains(category, "کاشی کف")
        self.assertContains(detail, "توضیحات فارسی")
        self.assertContains(detail, "موجود")
        self.assertContains(english, "English description")


class VariantPriceDomainTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="Currency Tile",
            slug="currency-tile",
            sku="CAT-CURRENCY-TILE",
            is_active=True,
        )
        self.variant_a = ProductVariant.objects.create(
            product=self.product,
            sku="CURRENCY-TILE-A",
            is_active=True,
        )
        self.variant_b = ProductVariant.objects.create(
            product=self.product,
            sku="CURRENCY-TILE-B",
            is_active=True,
        )

    def test_variant_can_have_independent_usd_and_toman_prices(self):
        usd = set_variant_price(
            variant=self.variant_a,
            currency=Currency.USD,
            amount=Decimal("30.00"),
        )
        toman = set_variant_price(
            variant=self.variant_a,
            currency=Currency.TOMAN,
            amount=Decimal("2500000"),
        )

        self.assertEqual(usd.currency, Currency.USD)
        self.assertEqual(toman.currency, Currency.TOMAN)
        self.assertEqual(self.variant_a.prices.count(), 2)

    def test_variant_currency_pair_is_unique(self):
        VariantPrice.objects.create(
            variant=self.variant_a,
            currency=Currency.USD,
            amount=Decimal("30.00"),
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            VariantPrice.objects.create(
                variant=self.variant_a,
                currency=Currency.USD,
                amount=Decimal("31.00"),
            )

    def test_price_validation_rejects_negative_amount(self):
        price = VariantPrice(
            variant=self.variant_a,
            currency=Currency.USD,
            amount=Decimal("-0.01"),
        )

        with self.assertRaises(ValidationError):
            price.full_clean()

    def test_toman_price_validation_rejects_fractional_amounts(self):
        price = VariantPrice(
            variant=self.variant_a,
            currency=Currency.TOMAN,
            amount=Decimal("2500000.50"),
        )

        with self.assertRaises(ValidationError):
            price.full_clean()

    def test_compare_at_price_must_be_greater_than_sale_price(self):
        price = VariantPrice(
            variant=self.variant_a,
            currency=Currency.USD,
            amount=Decimal("30.00"),
            compare_at_amount=Decimal("30.00"),
        )

        with self.assertRaises(ValidationError):
            price.full_clean()

    def test_explicit_lookup_never_falls_back_to_another_currency(self):
        usd = VariantPrice.objects.create(
            variant=self.variant_a,
            currency=Currency.USD,
            amount=Decimal("30.00"),
        )

        self.assertEqual(get_variant_price(self.variant_a, Currency.USD), usd)
        self.assertIsNone(get_variant_price(self.variant_a, Currency.TOMAN))

    def test_product_price_range_is_derived_per_currency(self):
        VariantPrice.objects.bulk_create([
            VariantPrice(variant=self.variant_a, currency=Currency.USD, amount=Decimal("32.00")),
            VariantPrice(variant=self.variant_b, currency=Currency.USD, amount=Decimal("42.00")),
            VariantPrice(variant=self.variant_a, currency=Currency.TOMAN, amount=Decimal("2500000")),
            VariantPrice(variant=self.variant_b, currency=Currency.TOMAN, amount=Decimal("3200000")),
        ])

        usd_range = get_product_price_range(self.product, Currency.USD)
        toman_range = get_product_price_range(self.product, Currency.TOMAN)

        self.assertEqual(usd_range.minimum, Decimal("32.00"))
        self.assertEqual(usd_range.maximum, Decimal("42.00"))
        self.assertEqual(toman_range.minimum, Decimal("2500000.00"))
        self.assertEqual(toman_range.maximum, Decimal("3200000.00"))

    def test_product_range_annotations_are_currency_specific(self):
        VariantPrice.objects.bulk_create([
            VariantPrice(variant=self.variant_a, currency=Currency.USD, amount=Decimal("32.00")),
            VariantPrice(variant=self.variant_b, currency=Currency.USD, amount=Decimal("42.00")),
            VariantPrice(variant=self.variant_a, currency=Currency.TOMAN, amount=Decimal("2500000")),
        ])

        product = annotate_product_price_range(
            Product.objects.filter(pk=self.product.pk), Currency.USD
        ).get()

        self.assertEqual(product.requested_currency_min_price, Decimal("32.00"))
        self.assertEqual(product.requested_currency_max_price, Decimal("42.00"))


class StorefrontVariantPriceTests(TestCase):
    def setUp(self):
        translation.activate("en")
        self.product = Product.objects.create(
            name="Storefront Tile",
            slug="storefront-tile",
            sku="CAT-STOREFRONT",
            is_active=True,
        )
        self.variant_a = ProductVariant.objects.create(
            product=self.product,
            sku="STOREFRONT-A",
            stock=10,
            is_active=True,
        )
        self.variant_b = ProductVariant.objects.create(
            product=self.product,
            sku="STOREFRONT-B",
            stock=10,
            is_active=True,
        )
        VariantPrice.objects.bulk_create([
            VariantPrice(variant=self.variant_a, currency=Currency.USD, amount=Decimal("32.50"), compare_at_amount=Decimal("40.00")),
            VariantPrice(variant=self.variant_b, currency=Currency.USD, amount=Decimal("42.00")),
            VariantPrice(variant=self.variant_a, currency=Currency.TOMAN, amount=Decimal("2500000")),
            VariantPrice(variant=self.variant_b, currency=Currency.TOMAN, amount=Decimal("3200000")),
        ])
        self.unpriced_product = Product.objects.create(
            name="IRT Only Tile", slug="irt-only-tile", sku="CAT-IRT-ONLY", is_active=True
        )
        self.irt_variant = ProductVariant.objects.create(
            product=self.unpriced_product, sku="IRT-ONLY", is_active=True
        )
        VariantPrice.objects.create(
            variant=self.irt_variant, currency=Currency.TOMAN, amount=Decimal("1500000")
        )

    def test_catalog_card_uses_variant_price_without_product_price_fallback(self):
        response = self.client.get(reverse("products:product_list"))

        self.assertContains(response, "From $32.50")
        self.assertNotContains(response, "999.00")
        self.assertContains(response, "Price unavailable")

    def test_currency_specific_filtering_sorting_and_sale_detection(self):
        usd_products, _ = get_filtered_products(
            min_price="40", currency=Currency.USD
        )
        self.assertIn(self.product, usd_products)
        self.assertNotIn(self.unpriced_product, usd_products)

        toman_products, _ = get_filtered_products(
            max_price="2000000", currency=Currency.TOMAN
        )
        self.assertIn(self.unpriced_product, toman_products)
        self.assertNotIn(self.product, toman_products)

        sale_products, _ = get_filtered_products(on_sale="1", currency=Currency.USD)
        self.assertIn(self.product, sale_products)
        toman_sales, _ = get_filtered_products(on_sale="1", currency=Currency.TOMAN)
        self.assertNotIn(self.product, toman_sales)

        sorted_products = list(get_sorted_products(
            get_filtered_products(currency=Currency.USD)[0], "price_asc"
        ))
        self.assertEqual(sorted_products[0], self.product)
        self.assertEqual(sorted_products[-1], self.unpriced_product)

    def test_detail_context_exposes_decimal_safe_selected_variant_data(self):
        response = self.client.get(
            f"{reverse('products:product_detail', args=[self.product.slug])}?variant={self.variant_a.pk}"
        )

        self.assertEqual(response.context["variants_data"][0]["amount"], "32.50")
        self.assertEqual(response.context["variants_data"][0]["compare_at_amount"], "40.00")
        self.assertEqual(response.context["variants_data"][0]["currency"], Currency.USD)
        self.assertEqual(response.context["initial_discount_percent"], 19)
        self.assertNotContains(response, "999.00")
        self.assertContains(response, "$32.50")

        non_sale = self.client.get(
            f"{reverse('products:product_detail', args=[self.product.slug])}?variant={self.variant_b.pk}"
        )
        self.assertEqual(non_sale.context["initial_discount_percent"], 0)

    def test_wishlist_and_search_suggestions_use_derived_usd_prices(self):
        user = get_user_model().objects.create_user(username="wishlist-user", password="secret")
        Wishlist.objects.create(user=user, product=self.product)
        self.client.force_login(user)

        response = self.client.get("/en/wishlist/")
        self.assertContains(response, "From $32.50")
        self.assertNotContains(response, "999.00")

        response = self.client.get(f"{reverse('products:search_suggestions')}?q=Storefront")
        result = response.json()["products"][0]
        self.assertEqual(result["price"], "32.50")
        self.assertEqual(result["currency"], Currency.USD)


class FilterScopedCatalogPricingTests(TestCase):
    def setUp(self):
        translation.activate("en")
        self.black = Color.objects.create(name="Black", slug="black")
        self.blue = Color.objects.create(name="Blue", slug="blue")
        self.beige = Color.objects.create(name="Beige", slug="beige")
        self.matte = Finish.objects.create(name="Matte", slug="matte")
        self.polished = Finish.objects.create(name="Polished", slug="polished")

    def product(self, name, slug):
        return Product.objects.create(name=name, slug=slug, sku=slug.upper(), is_active=True)

    def variant(self, product, sku, amount, *, color=None, finish=None, stock=5, compare_at=None):
        variant = ProductVariant.objects.create(
            product=product, sku=sku, color=color, finish=finish, stock=stock, is_active=True
        )
        VariantPrice.objects.create(
            variant=variant, currency=Currency.USD, amount=Decimal(str(amount)),
            compare_at_amount=Decimal(str(compare_at)) if compare_at else None,
        )
        return variant

    def scoped_product(self, **filters):
        products, variants = get_filtered_products(currency=Currency.USD, **filters)
        listed = list(products)
        attach_target_variant_ids(listed, variants, Currency.USD, use_catalog_variant_prices=True)
        return listed

    def test_no_variant_filter_keeps_global_price_range(self):
        product = self.product("Industrial Grey", "industrial-grey")
        self.variant(product, "GREY-BEIGE", 86, color=self.beige)
        self.variant(product, "GREY-BLACK", 107, color=self.black)
        listed, _ = get_filtered_products(currency=Currency.USD)
        card = listed.get(pk=product.pk)
        self.assertEqual(card.requested_currency_min_price, Decimal("86"))
        self.assertFalse(hasattr(card, "catalog_pricing_is_scoped"))

    def test_color_scope_uses_matching_price_and_target(self):
        product = self.product("Industrial Grey", "industrial-grey")
        self.variant(product, "GREY-BEIGE", 86, color=self.beige)
        black = self.variant(product, "GREY-BLACK", 107, color=self.black)
        card = self.scoped_product(selected_colors=["black"])[0]
        self.assertEqual(card.catalog_min_price, Decimal("107"))
        self.assertEqual(card.catalog_max_price, Decimal("107"))
        self.assertEqual(card.target_variant_id, black.pk)
        detail = self.client.get(
            f"{reverse('products:product_detail', args=[product.slug])}?variant={black.pk}"
        )
        self.assertEqual(detail.context["preselected_variant_id"], black.pk)
        self.assertEqual(detail.context["initial_price"].amount, Decimal("107"))

    def test_multiple_matches_intersection_and_target_use_lowest_matching_variant(self):
        product = self.product("Industrial Grey", "industrial-grey")
        black_matte = self.variant(product, "BLACK-MATTE", 107, color=self.black, finish=self.matte)
        self.variant(product, "BLACK-POLISHED", 95, color=self.black, finish=self.polished)
        self.variant(product, "BLUE-MATTE", 80, color=self.blue, finish=self.matte)
        card = self.scoped_product(selected_colors=["black"], selected_finishes=["matte"])[0]
        self.assertEqual(card.catalog_min_price, Decimal("107"))
        self.assertEqual(card.catalog_max_price, Decimal("107"))
        self.assertEqual(card.target_variant_id, black_matte.pk)

        self.variant(product, "BLACK-MATTE-HIGH", 120, color=self.black, finish=self.matte)
        card = self.scoped_product(selected_colors=["black"], selected_finishes=["matte"])[0]
        self.assertEqual(card.catalog_min_price, Decimal("107"))
        self.assertEqual(card.catalog_max_price, Decimal("120"))
        self.assertEqual(card.target_variant_id, black_matte.pk)

    def test_price_stock_and_sale_filters_scope_card_presentation(self):
        product = self.product("Filtered Tile", "filtered-tile")
        self.variant(product, "CHEAP", 80, color=self.beige, stock=0)
        expensive = self.variant(product, "EXPENSIVE", 110, color=self.black, stock=4, compare_at=140)

        for filters in ({"min_price": "100"}, {"in_stock": "1"}, {"on_sale": "1"}):
            card = self.scoped_product(**filters)[0]
            self.assertEqual(card.catalog_min_price, Decimal("110"))
            self.assertEqual(card.target_variant_id, expensive.pk)
            self.assertIsNotNone(card.catalog_sale_percent)

    def test_filtered_price_sort_uses_matching_minimum_before_pagination(self):
        product_a = self.product("Product A", "product-a")
        self.variant(product_a, "A-BEIGE", 40, color=self.beige)
        self.variant(product_a, "A-BLACK", 120, color=self.black)
        product_b = self.product("Product B", "product-b")
        self.variant(product_b, "B-BEIGE", 80, color=self.beige)
        self.variant(product_b, "B-BLACK", 90, color=self.black)
        products, _ = get_filtered_products(selected_colors=["black"], currency=Currency.USD)
        self.assertEqual(
            list(get_sorted_products(products, "price_asc", use_catalog_variant_prices=True)),
            [product_b, product_a],
        )
        self.assertEqual(
            list(get_sorted_products(products, "price_desc", use_catalog_variant_prices=True)),
            [product_a, product_b],
        )

    def test_filtered_card_does_not_advertise_unmatched_discount(self):
        product = self.product("Discount Tile", "discount-tile")
        self.variant(product, "BEIGE-SALE", 80, color=self.beige, compare_at=120)
        black = self.variant(product, "BLACK-FULL", 107, color=self.black)
        response = self.client.get(f"{reverse('products:product_list')}?colors=black")
        card = response.context["page_obj"].object_list[0]
        self.assertEqual(card.target_variant_id, black.pk)
        self.assertIsNone(card.catalog_sale_percent)
        self.assertNotContains(response, "Save 33%")

    def test_variant_selector_uses_currency_metadata_not_legacy_price_or_symbol(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "static" / "js" / "variant_selector.js"
        ).read_text(encoding="utf-8")

        self.assertIn("Intl.NumberFormat", source)
        self.assertNotRegex(source, r"variant\.price(?:\s|\)|,|;)")

    def test_cart_dynamic_money_uses_document_locale_without_en_us_fallback(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "static" / "js" / "cart.js"
        ).read_text(encoding="utf-8")

        self.assertIn("Intl.NumberFormat", source)
        self.assertIn("document.documentElement.lang", source)
        self.assertNotIn("en-US", source)


class CatalogActiveFilterUXTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(
            name="Atlas Display", name_fa="اطلس", slug="brand-internal"
        )
        self.color = Color.objects.create(
            name="White", name_fa="سفید", slug="color-internal"
        )
        self.second_color = Color.objects.create(
            name="Grey", name_fa="خاکستری", slug="second-color-internal"
        )
        self.size = Size.objects.create(
            name="60x60cm", name_fa="۶۰ در ۶۰", slug="size-internal"
        )
        self.product = Product.objects.create(
            name="Ceramic Atlas", name_fa="سرامیک اطلس", slug="ceramic-atlas",
            sku="CERAMIC-ATLAS", brand=self.brand, is_active=True,
        )
        variant = ProductVariant.objects.create(
            product=self.product, sku="CERAMIC-ATLAS-WHITE", color=self.color,
            size=self.size, stock=5, is_active=True,
        )
        VariantPrice.objects.create(
            variant=variant, currency=Currency.USD, amount=Decimal("80.00")
        )
        VariantPrice.objects.create(
            variant=variant, currency=Currency.TOMAN, amount=Decimal("4800000")
        )
        second_variant = ProductVariant.objects.create(
            product=self.product, sku="CERAMIC-ATLAS-GREY", color=self.second_color,
            size=self.size, stock=4, is_active=True,
        )
        VariantPrice.objects.create(
            variant=second_variant, currency=Currency.USD, amount=Decimal("85.00")
        )
        VariantPrice.objects.create(
            variant=second_variant, currency=Currency.TOMAN, amount=Decimal("5100000")
        )

    def _chip(self, response, label):
        return next(chip for chip in response.context["active_filter_chips"] if chip["label"] == label)

    def test_catalog_sort_uses_the_shared_htmx_workspace_and_preserves_filters(self):
        response = self.client.get("/en/shop/?brands=brand-internal&sort=price_desc")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="catalog-workspace"')
        self.assertContains(response, 'hx-get="/en/shop/"')
        self.assertContains(response, 'hx-target="#catalog-workspace"')
        self.assertContains(response, 'name="brands" value="brand-internal"')
        self.assertContains(response, 'name="sort" data-submit-on-change')

        fragment = self.client.get(
            "/en/shop/?brands=brand-internal&sort=price_asc",
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="catalog-workspace",
        )
        self.assertEqual(fragment.status_code, 200)
        self.assertContains(fragment, 'id="catalog-workspace"')
        self.assertContains(fragment, 'value="price_asc" selected')
        self.assertContains(fragment, 'name="brands" value="brand-internal"')
        self.assertNotContains(fragment, "<html")

    def test_filtered_catalog_product_links_keep_only_the_variant_detail_parameter(self):
        response = self.client.get("/en/shop/?brands=brand-internal&sort=price_desc")
        self.assertContains(response, 'hx-boost="false"')
        first_variant = self.product.variants.order_by("pk").first()
        self.assertContains(
            response,
            f'href="{self.product.get_absolute_url()}?variant={first_variant.pk}" hx-boost="false"',
        )
        self.assertNotContains(
            response,
            f'{self.product.get_absolute_url()}?variant={first_variant.pk}&amp;brands=brand-internal',
        )
        self.assertNotContains(
            response,
            f'{self.product.get_absolute_url()}?variant={first_variant.pk}&amp;sort=price_desc',
        )

        fragment = self.client.get(
            "/en/shop/?brands=brand-internal&sort=price_desc",
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="catalog-workspace",
        )
        self.assertContains(
            fragment,
            f'href="{self.product.get_absolute_url()}?variant={first_variant.pk}" hx-boost="false"',
        )
        self.assertNotContains(fragment, "<html")

        # A Catalog request must not affect the subsequent normal Product Detail response.
        detail = self.client.get(f"{self.product.get_absolute_url()}?variant={first_variant.pk}")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, 'data-product-detail')
        self.assertNotContains(detail, 'id="catalog-workspace"')

    def test_catalog_history_restore_uses_the_full_page_authority(self):
        url = "/en/shop/?brands=brand-internal&sort=price_desc"
        fragment = self.client.get(
            url,
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="catalog-workspace",
        )
        self.assertContains(fragment, 'id="catalog-workspace"')
        self.assertNotContains(fragment, "<html")

        restored = self.client.get(
            url,
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="catalog-workspace",
            HTTP_HX_HISTORY_RESTORE_REQUEST="true",
        )
        self.assertContains(restored, "<html")
        self.assertContains(restored, "<!-- navbar.html !-->")
        self.assertContains(restored, 'id="catalog-workspace"')
        self.assertContains(restored, 'hx-history="false"')
        self.assertIn("no-store", restored.headers["Cache-Control"])

        base = (Path(__file__).resolve().parents[1] / "templates" / "base.html").read_text(encoding="utf-8")
        htmx_config = (Path(__file__).resolve().parents[1] / "static" / "js" / "htmx-config.js").read_text(encoding="utf-8")
        helper = (Path(__file__).resolve().parents[1] / "TileCompany" / "htmx.py").read_text(encoding="utf-8")
        self.assertIn("{% static 'js/htmx-config.js' %}", base)
        self.assertLess(
            base.index("vendor/node_modules/htmx.org/dist/htmx.min.js"),
            base.index("{% static 'js/htmx-config.js' %}"),
        )
        self.assertIn("htmx.config.historyRestoreAsHxRequest = false", htmx_config)
        self.assertIn("is_htmx_history_restore", helper)

    def test_active_filters_use_human_labels_and_remove_one_value(self):
        from urllib.parse import parse_qs, urlsplit

        response = self.client.get(
            "/en/shop/?q=Ceramic&brands=brand-internal&colors=color-internal"
            "&sizes=size-internal&sort=price_asc&page=9"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_active_criteria"])
        self.assertContains(response, "Results for")
        self.assertContains(response, "Atlas Display")
        self.assertContains(response, "White")
        self.assertContains(response, "60x60cm")

        color_chip = self._chip(response, "Color")
        query = parse_qs(urlsplit(color_chip["remove_url"]).query)
        self.assertNotIn("colors", query)
        self.assertNotIn("page", query)
        self.assertEqual(query["q"], ["Ceramic"])
        self.assertEqual(query["brands"], ["brand-internal"])
        self.assertEqual(query["sizes"], ["size-internal"])
        self.assertEqual(query["sort"], ["price_asc"])

    def test_clear_all_removes_narrowing_criteria_but_keeps_sort(self):
        from urllib.parse import parse_qs, urlsplit

        response = self.client.get(
            "/en/shop/?q=Ceramic&brands=brand-internal&sort=price_desc&page=4"
        )
        query = parse_qs(urlsplit(response.context["clear_all_url"]).query)
        self.assertEqual(query, {"sort": ["price_desc"]})

    def test_removing_one_repeated_filter_value_keeps_its_sibling(self):
        from urllib.parse import parse_qs, urlsplit

        response = self.client.get(
            "/en/shop/?colors=color-internal&colors=second-color-internal"
            "&brands=brand-internal&sort=rating&page=7"
        )
        white_chip = next(
            chip for chip in response.context["active_filter_chips"]
            if chip["label"] == "Color" and chip["display_value"] == "White"
        )
        query = parse_qs(urlsplit(white_chip["remove_url"]).query)
        self.assertEqual(query["colors"], ["second-color-internal"])
        self.assertEqual(query["brands"], ["brand-internal"])
        self.assertEqual(query["sort"], ["rating"])
        self.assertNotIn("page", query)

    def test_no_empty_summary_and_persian_labels_are_human_readable(self):
        empty = self.client.get("/en/shop/")
        self.assertFalse(empty.context["has_active_criteria"])
        self.assertNotContains(empty, "Active search and filters")

        response = self.client.get(
            "/fa/shop/?brands=brand-internal&colors=color-internal&sizes=size-internal"
        )
        values = {chip["display_value"] for chip in response.context["active_filter_chips"]}
        self.assertIn("اطلس", values)
        self.assertIn("سفید", values)
        self.assertIn("۶۰ در ۶۰", values)
        self.assertNotIn("brand-internal", values)
        self.assertNotIn("color-internal", values)


class WishlistLocalizationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="wishlist-buyer", password="secret-pass-123")
        self.category = Category.objects.create(name="Wall tiles", name_fa="کاشی دیوار", slug="wall-tiles-wishlist")
        self.product = Product.objects.create(name="Persian fallback tile", name_fa="کاشی فارسی", slug="wishlist-tile", sku="WISH-001")
        self.product.categories.add(self.category)
        self.variant = ProductVariant.objects.create(product=self.product, sku="WISH-001-A", is_active=True)
        VariantPrice.objects.create(variant=self.variant, currency=Currency.USD, amount=Decimal("25.00"))
        VariantPrice.objects.create(variant=self.variant, currency=Currency.TOMAN, amount=Decimal("2500000"))

    def test_persian_empty_wishlist_and_login_redirect(self):
        response = self.client.get("/fa/wishlist/")
        self.assertRedirects(response, "/fa/accounts/login/?next=/fa/wishlist/")
        self.client.force_login(self.user)
        response = self.client.get("/fa/wishlist/")
        self.assertContains(response, "علاقه‌مندی‌ها")
        self.assertContains(response, "هنوز محصول ذخیره‌شده‌ای ندارید")
        self.assertContains(response, "مشاهدهٔ محصولات")

    def test_persian_populated_wishlist_uses_localized_product_data_and_price(self):
        Wishlist.objects.create(user=self.user, product=self.product)
        self.client.force_login(self.user)
        response = self.client.get("/fa/wishlist/")
        self.assertContains(response, "کاشی فارسی")
        self.assertContains(response, "کاشی دیوار")
        self.assertContains(response, "۲٬۵۰۰٬۰۰۰")
        self.assertContains(response, "تومان")
        self.assertContains(response, "حذف از علاقه‌مندی‌ها")

    def test_wishlist_toggle_localizes_messages_and_preserves_ownership(self):
        self.client.force_login(self.user)
        url = f"/fa/wishlist/toggle/{self.product.pk}/"
        response = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertTrue(response.json()["in_wishlist"])
        self.assertEqual(response.json()["message"], "به علاقه‌مندی‌ها افزوده شد.")
        self.assertEqual(Wishlist.objects.filter(user=self.user, product=self.product).count(), 1)
        response = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertFalse(response.json()["in_wishlist"])
        self.assertEqual(response.json()["message"], "از علاقه‌مندی‌ها حذف شد.")
        self.assertFalse(Wishlist.objects.filter(user=self.user, product=self.product).exists())

    def test_english_wishlist_remains_english(self):
        self.client.force_login(self.user)
        response = self.client.get("/en/wishlist/")
        self.assertContains(response, "Wishlist")
        self.assertContains(response, "No saved products yet")


class WishlistCustomerSurfaceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="wishlist-surface", password="secret-pass-123")
        self.other_user = get_user_model().objects.create_user(username="wishlist-other", password="secret-pass-123")
        self.product_type = ProductType.objects.create(code="wishlist-device", name="Wishlist Device")

    def create_product(self, index, *, stock=3, active=True, sale=False):
        product = Product.objects.create(
            product_type=self.product_type, name=f"Saved device {index}",
            slug=f"saved-device-{index}", sku=f"WISH-SURFACE-{index}", is_active=active,
        )
        variant = ProductVariant.objects.create(
            product=product, sku=f"WISH-SURFACE-{index}-A", stock=stock, is_active=True,
            price_unit=ProductVariant.PriceUnit.UNIT,
        )
        VariantPrice.objects.create(
            variant=variant, currency=Currency.USD, amount=Decimal("80.00"),
            compare_at_amount=Decimal("100.00") if sale else None,
        )
        return product

    def test_account_shell_current_sale_availability_and_canonical_product_link(self):
        product = self.create_product(1, sale=True)
        Wishlist.objects.create(user=self.user, product=product)
        self.client.force_login(self.user)

        response = self.client.get("/en/wishlist/")

        self.assertContains(response, 'data-account-navigation')
        self.assertContains(response, 'account-nav-link account-nav-link--active" aria-current="page"')
        self.assertContains(response, product.get_absolute_url())
        self.assertContains(response, "$80.00")
        self.assertContains(response, "$100.00")
        self.assertContains(response, "Save 20%")
        self.assertContains(response, "Available")
        self.assertNotContains(response, "Add to cart")

    def test_sold_out_is_visible_but_not_marked_available_and_remove_is_owned_post_only(self):
        product = self.create_product(2, stock=0)
        Wishlist.objects.create(user=self.user, product=product)
        Wishlist.objects.create(user=self.other_user, product=product)
        self.client.force_login(self.user)
        url = reverse("products:wishlist_toggle", args=[product.pk])

        response = self.client.get("/en/wishlist/")
        self.assertContains(response, "Unavailable")
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {"next": reverse("products:wishlist")})
        self.assertRedirects(response, reverse("products:wishlist"))
        self.assertFalse(Wishlist.objects.filter(user=self.user, product=product).exists())
        self.assertTrue(Wishlist.objects.filter(user=self.other_user, product=product).exists())

    def test_twelve_saved_products_are_paginated_and_query_growth_is_bounded(self):
        first = self.create_product(10)
        Wishlist.objects.create(user=self.user, product=first)
        self.client.force_login(self.user)
        with CaptureQueriesContext(connection) as single:
            self.client.get(reverse("products:wishlist"))
        for index in range(11, 23):
            Wishlist.objects.create(user=self.user, product=self.create_product(index))
        with CaptureQueriesContext(connection) as dozen:
            response = self.client.get(reverse("products:wishlist"))

        self.assertEqual(response.context["page_obj"].paginator.count, 13)
        self.assertTrue(response.context["page_obj"].has_next())
        self.assertLessEqual(len(dozen), len(single) + 3)

    def test_low_stock_is_product_total_and_shared_by_catalog_card_and_wishlist(self):
        product = self.create_product(30, stock=1)
        low_variant = ProductVariant.objects.create(
            product=product, sku="WISH-SURFACE-30-B", stock=3, is_active=True,
            price_unit=ProductVariant.PriceUnit.UNIT,
        )
        VariantPrice.objects.create(variant=low_variant, currency=Currency.USD, amount=Decimal("85.00"))
        Wishlist.objects.create(user=self.user, product=product)
        self.client.force_login(self.user)

        wishlist = self.client.get("/en/wishlist/")
        catalog = self.client.get("/en/shop/")

        self.assertContains(wishlist, "Only 4 items left")
        self.assertContains(catalog, "Only 4 items left")

    def test_low_representative_variant_does_not_create_false_product_scarcity(self):
        product = self.create_product(31, stock=1)
        healthy_variant = ProductVariant.objects.create(
            product=product, sku="WISH-SURFACE-31-B", stock=20, is_active=True,
            price_unit=ProductVariant.PriceUnit.UNIT,
        )
        VariantPrice.objects.create(variant=healthy_variant, currency=Currency.USD, amount=Decimal("90.00"))
        Wishlist.objects.create(user=self.user, product=product)
        self.client.force_login(self.user)

        response = self.client.get("/en/wishlist/")

        self.assertNotContains(response, "Only 1 item left")
        self.assertNotContains(response, "Only 21 items left")

    def test_zero_and_threshold_stock_have_no_low_stock_copy(self):
        zero = self.create_product(32, stock=0)
        threshold = self.create_product(33, stock=5)
        Wishlist.objects.bulk_create([
            Wishlist(user=self.user, product=zero),
            Wishlist(user=self.user, product=threshold),
        ])
        self.client.force_login(self.user)

        response = self.client.get("/en/wishlist/")

        self.assertContains(response, "Unavailable")
        self.assertNotContains(response, "Only 5 items left")

    def test_persian_low_stock_uses_localized_digits(self):
        product = self.create_product(34, stock=3)
        variant = product.variants.get()
        VariantPrice.objects.create(variant=variant, currency=Currency.TOMAN, amount=Decimal("4000000"))
        Wishlist.objects.create(user=self.user, product=product)
        self.client.force_login(self.user)

        response = self.client.get("/fa/wishlist/")

        self.assertContains(response, "فقط ۳")
        self.assertNotContains(response, "Only 3")

    def test_wishlist_uses_the_compact_card_family_at_every_breakpoint(self):
        source = (Path(__file__).resolve().parent / "templates" / "products" / "wishlist.html").read_text(encoding="utf-8")
        shared_card = (Path(__file__).resolve().parent / "templates" / "products" / "components" / "product_card.html").read_text(encoding="utf-8")
        compact_card = (Path(__file__).resolve().parent / "templates" / "products" / "components" / "wishlist_product_card.html").read_text(encoding="utf-8")

        self.assertIn("lg:grid-cols-2", source)
        self.assertIn("xl:grid-cols-3", source)
        self.assertIn('products/components/wishlist_product_card.html', source)
        self.assertNotIn('products/components/product_card.html', source)
        self.assertNotIn('products/components/product_card_mobile.html', source)
        self.assertIn("data-wishlist-compact-card", compact_card)
        self.assertIn("min-h-40", compact_card)
        self.assertIn("line-clamp-2", compact_card)
        self.assertIn("data-wishlist-card-media", compact_card)
        self.assertIn('aspect-[4/5]', shared_card)


class LanguageSwitcherTests(TestCase):
    def test_desktop_and_mobile_navigation_offer_a_path_preserving_language_switch(self):
        response = self.client.get("/en/shop/")

        self.assertContains(response, 'action="/i18n/setlang/"', count=2)
        self.assertContains(response, 'name="next" type="hidden" value="/en/shop/"', count=2)
        self.assertContains(response, 'name="language" type="hidden" value="fa"', count=2)
        self.assertContains(response, "Switch to Persian", count=4)

        switched = self.client.post(
            reverse("set_language"),
            {"language": "fa", "next": "/en/shop/"},
        )
        self.assertRedirects(switched, "/fa/shop/")

    def test_persian_navigation_switches_back_to_english(self):
        response = self.client.get("/fa/shop/")

        self.assertContains(response, 'name="language" type="hidden" value="en"', count=2)
        self.assertContains(response, "تغییر زبان به انگلیسی", count=4)
