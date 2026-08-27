from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.utils import translation

from products.currency import Currency
from products.models import Brand, Category, Color, Design, Finish, Material, Product, ProductVariant, Size, Usage, VariantPrice
from products.presentation import get_localized_value
from products.selectors import get_product_variant_context


class LocalizedContentResolverTests(TestCase):
    def test_active_language_and_blank_fallback(self):
        color = Color(name="Black", name_fa="مشکی", slug="black")
        with translation.override("en"):
            self.assertEqual(get_localized_value(color, "name"), "Black")
        with translation.override("fa-ir"):
            self.assertEqual(get_localized_value(color, "name"), "مشکی")
        color.name_fa = "   "
        with translation.override("fa"):
            self.assertEqual(get_localized_value(color, "name"), "Black")
        with translation.override("ar"):
            self.assertEqual(get_localized_value(color, "name"), "Black")

    def test_product_and_supported_attributes_prefer_persian(self):
        product = Product(name="Industrial Grey", name_fa="خاکستری صنعتی", description="English", description_fa="فارسی", series_name="Urban", series_name_fa="شهری")
        attributes = [
            Category(name="Floor", name_fa="کف", slug="floor"), Brand(name="TileCo", name_fa="تایل‌کو", slug="tileco"),
            Design(name="Modern", name_fa="مدرن", slug="modern"), Usage(name="Indoor", name_fa="داخلی", slug="indoor"),
            Material(name="Porcelain", name_fa="پرسلان", slug="porcelain"), Color(name="Black", name_fa="مشکی", slug="black"),
            Finish(name="Matte", name_fa="مات", slug="matte"), Size(name="60x60", name_fa="۶۰×۶۰", slug="60x60"),
        ]
        with translation.override("fa"):
            self.assertEqual(get_localized_value(product, "name"), "خاکستری صنعتی")
            self.assertEqual(get_localized_value(product, "description"), "فارسی")
            self.assertEqual(get_localized_value(product, "series_name"), "شهری")
            self.assertTrue(all(get_localized_value(item, "name") == item.name_fa for item in attributes))

    def test_variant_context_keeps_slugs_and_localizes_labels(self):
        product = Product.objects.create(name="Tile", name_fa="کاشی", slug="tile-6a", sku="TILE-6A", is_active=True)
        color = Color.objects.create(name="Black", name_fa="مشکی", slug="black-6a")
        finish = Finish.objects.create(name="Matte", name_fa="مات", slug="matte-6a")
        size = Size.objects.create(name="60x60", name_fa="۶۰×۶۰", slug="60x60-6a")
        variant = ProductVariant.objects.create(product=product, sku="TILE-6A-1", color=color, finish=finish, size=size, is_active=True)
        VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal("10.00"))
        request = RequestFactory().get("/fa/product/tile-6a/")
        with translation.override("fa"):
            data = get_product_variant_context(product, request, Currency.USD)["variants_data"][0]
        self.assertEqual((data["color"], data["finish"], data["size"]), ("black-6a", "matte-6a", "60x60-6a"))
        self.assertEqual((data["color_label"], data["finish_label"], data["size_label"]), ("مشکی", "مات", "۶۰×۶۰"))
