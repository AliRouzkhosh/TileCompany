from django.test import TestCase
from django.utils import translation


class NavbarSearchPlaceholderTests(TestCase):
    def test_english_search_placeholder(self):
        with translation.override("en"):
            response = self.client.get("/en/shop/")
        self.assertContains(
            response,
            'placeholder="Search products, attributes or SKU..."',
            html=False,
        )

    def test_persian_search_placeholder(self):
        with translation.override("fa"):
            response = self.client.get("/fa/shop/")
        self.assertContains(
            response,
            'placeholder="جست‌وجوی محصول، ویژگی یا کد کالا..."',
            html=False,
        )
