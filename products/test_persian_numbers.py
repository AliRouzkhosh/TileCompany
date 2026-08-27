from decimal import Decimal

from django.test import SimpleTestCase
from django.utils.translation import override

from products.currency import Currency
from products.templatetags.pricing import localized_digits, localized_integer, localized_number, money


class PersianNumberPresentationTests(SimpleTestCase):
    def test_localized_number_preserves_english_and_formats_persian(self):
        with override("en"):
            self.assertEqual(localized_number(1250000), "1,250,000")
            self.assertEqual(localized_number(Decimal("4.5")), "4.5")
        with override("fa-ir"):
            self.assertEqual(localized_number(1250000), "۱٬۲۵۰٬۰۰۰")
            self.assertEqual(localized_number(Decimal("4.5")), "۴٫۵")

    def test_money_keeps_currency_semantics_while_localizing_digits(self):
        with override("fa"):
            self.assertEqual(money(Decimal("4846000"), Currency.TOMAN), "۴٬۸۴۶٬۰۰۰ تومان")
            self.assertEqual(money(Decimal("86.00"), Currency.USD), "$۸۶٫۰۰")
        with override("en"):
            self.assertEqual(money(Decimal("4846000"), Currency.TOMAN), "4,846,000 Toman")

    def test_embedded_digits_and_percentages_are_localized_for_display(self):
        with override("fa"):
            self.assertEqual(localized_digits("60x60cm"), "۶۰x۶۰cm")
            self.assertEqual(localized_integer(Decimal("19.6")), "۲۰")
        with override("en"):
            self.assertEqual(localized_digits("60x60cm"), "60x60cm")
            self.assertEqual(localized_integer(Decimal("19.4")), "19")
