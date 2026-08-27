from datetime import datetime

from django.test import SimpleTestCase
from django.utils import timezone
from django.utils.translation import override

from products.templatetags.localized_dates import localized_date, localized_datetime, localized_time


class LocalizedDateTests(SimpleTestCase):
    def setUp(self):
        self.value = timezone.make_aware(datetime(2026, 7, 30, 14, 30))

    def test_persian_uses_jalali_date_and_persian_time_digits(self):
        with override("fa-ir"):
            self.assertEqual(localized_date(self.value), "۱۴۰۵/۰۵/۰۸")
            self.assertEqual(localized_time(self.value), "۱۴:۳۰")
            self.assertEqual(localized_datetime(self.value), "۱۴۰۵/۰۵/۰۸، ۱۴:۳۰")

    def test_english_keeps_gregorian_and_none_is_safe(self):
        with override("en"):
            self.assertIn("2026", localized_date(self.value))
            self.assertEqual(localized_date(None), "")
