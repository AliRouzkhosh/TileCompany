from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test import RequestFactory
from django.urls import reverse
from django.utils import translation

from products.currency import Currency
from products.models import Brand, Category, Color, Design, Finish, Material, Product, ProductImage, ProductVariant, Size, Usage, VariantPrice
from orders.cart import _build_cart
from orders.selectors import get_checkout_context
from products.storefront import get_storefront_currency
from products.search.categories import grouped_suggestions
from products.search.suggestions import product_suggestions
from dashboard.forms import catalog_term_form
from products.catalog_compat import save_tile_catalog_term
from products.catalog_backfill import inspect_tile_catalog


class StorefrontLocaleCurrencyTests(TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(translation.override("en"))
        self.color = Color.objects.create(name="Black", name_fa="مشکی", slug="black")
        self.finish = Finish.objects.create(name="Matte", name_fa="مات", slug="matte")
        self.material = Material.objects.create(name="Porcelain", name_fa="پرسلان", slug="porcelain")
        self.product = Product.objects.create(
            name="Industrial Grey", name_fa="خاکستری صنعتی",
            slug="industrial-grey-locale", sku="LOCALE-001", is_active=True,
        )
        self.product.materials.add(self.material)
        self.black = ProductVariant.objects.create(product=self.product, sku="LOCALE-BLACK", color=self.color, finish=self.finish, stock=4, is_active=True)
        self.white = ProductVariant.objects.create(product=self.product, sku="LOCALE-WHITE", stock=4, is_active=True)
        VariantPrice.objects.bulk_create([
            VariantPrice(variant=self.black, currency=Currency.USD, amount=Decimal("100.00")),
            VariantPrice(variant=self.black, currency=Currency.TOMAN, amount=Decimal("5000000")),
            VariantPrice(variant=self.white, currency=Currency.USD, amount=Decimal("90.00")),
            VariantPrice(variant=self.white, currency=Currency.TOMAN, amount=Decimal("4500000")),
        ])

    def test_currency_uses_active_language_with_safe_default(self):
        for language, expected in (("en", Currency.USD), ("fa", Currency.TOMAN), ("fa-ir", Currency.TOMAN), ("de", Currency.USD)):
            with translation.override(language):
                self.assertEqual(get_storefront_currency(), expected)

    def test_detail_breadcrumb_gallery_fallback_and_variant_json_follow_locale(self):
        ProductImage.objects.create(
            product=self.product,
            image=SimpleUploadedFile("gallery.png", b"not-a-real-image", content_type="image/png"),
        )
        en = self.client.get(f"/en/product/{self.product.slug}/?variant={self.black.pk}")
        fa = self.client.get(f"/fa/product/{self.product.slug}/?variant={self.black.pk}")
        self.assertContains(en, "Industrial Grey")
        self.assertContains(fa, "خاکستری صنعتی")
        self.assertContains(fa, 'alt="خاکستری صنعتی"')
        self.assertEqual(en.context["storefront_currency"], Currency.USD)
        self.assertEqual(fa.context["storefront_currency"], Currency.TOMAN)
        self.assertEqual(en.context["initial_price"].amount, Decimal("100.00"))
        self.assertEqual(fa.context["initial_price"].amount, Decimal("5000000"))
        self.assertEqual(en.context["variants_data"][0]["currency"], Currency.USD)
        self.assertEqual(fa.context["variants_data"][0]["currency"], Currency.TOMAN)
        self.assertEqual(fa.context["variants_data"][0]["color"], "black")
        self.assertEqual(fa.context["variants_data"][0]["color_label"], "مشکی")

    def test_catalog_filter_keeps_slug_and_scopes_prices_per_locale(self):
        en = self.client.get(f"/en/shop/?colors=black")
        fa = self.client.get(f"/fa/shop/?colors=black")
        en_card = next(item for item in en.context["page_obj"].object_list if item.pk == self.product.pk)
        fa_card = next(item for item in fa.context["page_obj"].object_list if item.pk == self.product.pk)
        self.assertEqual((en_card.target_variant_id, fa_card.target_variant_id), (self.black.pk, self.black.pk))
        self.assertEqual(en_card.catalog_min_price, Decimal("100.00"))
        self.assertEqual(fa_card.catalog_min_price, Decimal("5000000"))
        self.assertContains(fa, 'value="black"')
        self.assertContains(fa, "مشکی")

    def test_search_suggestions_use_localized_catalog_content_without_changing_slugs(self):
        category = Category.objects.create(name="Floor", name_fa="Persian Floor", slug="floor")
        brand = Brand.objects.create(name="Nova", name_fa="Persian Nova", slug="nova")
        self.product.categories.add(category)
        self.product.brand = brand
        self.product.save(update_fields=["brand"])

        with translation.override("fa"):
            suggestion = product_suggestions(self.product.name_fa, Currency.TOMAN)[0]
            group = next(group for group in grouped_suggestions(brand.name_fa) if group["title"] == "Brands")

        self.assertEqual(suggestion["name"], self.product.name_fa)
        self.assertIn(brand.name_fa, suggestion["subtitle"])
        self.assertIn(category.name_fa, suggestion["subtitle"])
        self.assertEqual(group["items"][0]["label"], brand.name_fa)
        self.assertEqual(group["items"][0]["value"], brand.slug)

    def test_cart_and_checkout_context_follow_the_locale_currency(self):
        with translation.override("en"):
            self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.black.pk, "quantity": 1})
            self.assertEqual(_build_cart(self.client)["currency"], Currency.USD)
            self.assertEqual(_build_cart(self.client)["items"][0]["unit_price"], Decimal("100.00"))

        with translation.override("fa"):
            self.client.get(reverse("products:product_list"))
            self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.black.pk, "quantity": 1})
            cart = _build_cart(self.client)
            self.assertEqual(cart["currency"], Currency.TOMAN)
            self.assertEqual(cart["items"][0]["unit_price"], Decimal("5000000"))
            request = RequestFactory().get("/fa/checkout/shipping/")
            request.session = self.client.session
            request.user = get_user_model().objects.create_user(username="locale-checkout-user", password="secret")
            self.assertEqual(get_checkout_context(request).currency, Currency.TOMAN)


class SeedDataPersianContentTests(TestCase):
    def test_fresh_seed_creates_bilingual_catalog_data_without_overwriting(self):
        call_command("seed_data")
        self.assertTrue(Product.objects.exists())
        self.assertFalse(Product.objects.filter(name_fa="").exists())
        self.assertFalse(Product.objects.filter(description_fa="").exists())
        self.assertFalse(Product.objects.filter(series_name_fa="").exists())
        for model in (Category, Brand, Design, Usage, Material, Color, Finish, Size):
            self.assertFalse(model.objects.filter(name_fa="").exists())

        color = Color.objects.first()
        form = catalog_term_form(Color)(
            data={"name": color.name, "name_fa": "نام سفارشی", "slug": color.slug},
            instance=color,
        )
        self.assertTrue(form.is_valid(), form.errors)
        save_tile_catalog_term(form)
        call_command("seed_data")
        color.refresh_from_db()
        self.assertEqual(color.name_fa, "نام سفارشی")
        self.assertEqual(ProductVariant.objects.count(), 50)
        self.assertFalse(inspect_tile_catalog(require_complete_generic=True).blocking_issues)


class GalleryFallbackSourceTests(TestCase):
    def test_gallery_fallback_uses_the_localized_product_value(self):
        source = (Path(__file__).resolve().parent / "templates" / "products" / "components" / "product_gallery.html").read_text(encoding="utf-8")
        self.assertIn('product|localized:"name"', source)
