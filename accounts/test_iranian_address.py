from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils.translation import override

from .forms import AddressForm, ProfileUpdateForm
from .models import Profile


class IranianAddressFormTests(TestCase):
    def data(self, **changes):
        values = {
            "label": "home", "first_name": "Ali", "last_name": "Buyer",
            "company_name": "", "phone": "09121234567", "country": "Iran",
            "province": "Tehran", "city": "Tehran", "street_address": "Valiasr",
            "postal_code": "12345-67890",
        }
        values.update(changes)
        return values

    def test_iranian_identifiers_normalize_ascii_and_persian_digits(self):
        for phone in ("09121234567", "+989121234567", "989121234567", "۰۹۱۲۱۲۳۴۵۶۷"):
            form = AddressForm(self.data(phone=phone, postal_code="۱۲۳۴۵۶۷۸۹۰"))
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data["country"], "IR")
            self.assertEqual(form.cleaned_data["phone"], "+989121234567")
            self.assertEqual(form.cleaned_data["postal_code"], "1234567890")

    def test_invalid_iranian_identifiers_are_rejected(self):
        for phone in ("08121234567", "0912123456", "091212345678", "+98121234567", "letters"):
            self.assertFalse(AddressForm(self.data(phone=phone)).is_valid())
        for postal in ("123456789", "12345678901", "abcdefghij"):
            self.assertFalse(AddressForm(self.data(postal_code=postal)).is_valid())

    def test_non_iranian_address_keeps_international_values(self):
        form = AddressForm(self.data(country="Canada", phone="+1 416 555 0100", postal_code="M5V 3A8"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["phone"], "+1 416 555 0100")
        self.assertEqual(form.cleaned_data["postal_code"], "M5V 3A8")

    def test_profile_reuses_iranian_phone_normalization(self):
        user = get_user_model().objects.create_user("profile-phone")
        profile = Profile.objects.create(user=user)
        form = ProfileUpdateForm({"phone": "۰۹۱۲۱۲۳۴۵۶۷"}, instance=profile)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["phone"], "+989121234567")

    def test_localized_form_keeps_identifier_inputs_ltr(self):
        with override("fa"):
            form = AddressForm()
            self.assertEqual(form.fields["phone"].widget.attrs["dir"], "ltr")
            self.assertEqual(form.fields["postal_code"].widget.attrs["inputmode"], "numeric")

    def test_country_semantics_not_locale_choose_validation(self):
        with override("fa"):
            international = AddressForm(self.data(country="Canada", phone="not-a-mobile", postal_code="postal"))
            self.assertTrue(international.is_valid(), international.errors)
        with override("en"):
            iranian = AddressForm(self.data(country="Iran", phone="not-a-mobile"))
            self.assertFalse(iranian.is_valid())
            self.assertIn("Enter a valid Iranian mobile number.", iranian.errors["phone"])
