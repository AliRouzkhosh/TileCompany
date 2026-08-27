from datetime import date

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone, translation

from accounts.birthdates import JalaliBirthdateField, format_birthdate
from accounts.forms import ProfileUpdateForm, RegisterForm
from accounts.models import Profile


class LanguageIsolationMixin:
    """Restore the translation context changed by LocaleMiddleware requests."""

    def setUp(self):
        super().setUp()
        self.enterContext(translation.override("en"))


class BirthdateConversionTests(TestCase):
    def jalali_field(self):
        return JalaliBirthdateField(css_class="control", label="Birth date")

    def test_known_conversion_display_and_digit_normalization(self):
        field = self.jalali_field()
        self.assertEqual(field.clean(["۱۳۷۴", "1", "۱"]), date(1995, 3, 21))
        self.assertEqual(field.clean(["1374", "1", "1"]), date(1995, 3, 21))
        self.assertEqual(format_birthdate(date(1995, 3, 21), language="fa"), "۱۳۷۴/۰۱/۰۱")

    def test_jalali_leap_and_invalid_dates(self):
        field = self.jalali_field()
        self.assertEqual(field.clean(["1399", "12", "30"]), date(2021, 3, 20))
        for value in (("1400", "12", "30"), ("1402", "13", "1"), ("1402", "1", "32"), ("1402", "", "1")):
            with self.assertRaises(ValidationError):
                field.clean(value)

    def test_blank_remains_optional(self):
        self.assertIsNone(self.jalali_field().clean(["", "", ""]))


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class BirthdateRegistrationTests(LanguageIsolationMixin, TestCase):
    def payload(self, username, **extra):
        values = {
            "username": username,
            "email": f"{username}@example.com",
            "password1": "valid-password-123",
            "password2": "valid-password-123",
            "birthdate": "1995-03-21",
        }
        values.update(extra)
        return values

    def test_english_signup_keeps_native_gregorian_contract(self):
        with translation.override("en"):
            form = RegisterForm()
            self.assertEqual(form.fields["birthdate"].widget.input_type, "date")
        response = self.client.post("/en/accounts/register/", self.payload("english-user"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_user_model().objects.get(username="english-user").profile.birthdate, date(1995, 3, 21))

    def test_persian_signup_uses_jalali_and_rejects_malformed_input(self):
        values = self.payload("persian-user", birthdate_0="۱۳۷۴", birthdate_1="1", birthdate_2="۱")
        values.pop("birthdate")
        response = self.client.post("/fa/accounts/register/", values)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_user_model().objects.get(username="persian-user").profile.birthdate, date(1995, 3, 21))

        invalid = self.payload("invalid-jalali", birthdate_0="۱۴۰۰", birthdate_1="12", birthdate_2="۳۰")
        invalid.pop("birthdate")
        response = self.client.post("/fa/accounts/register/", invalid)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تاریخ تولد معتبر")
        self.assertFalse(get_user_model().objects.filter(username="invalid-jalali").exists())

    def test_persian_signup_renders_one_grouped_jalali_control(self):
        response = self.client.get("/fa/accounts/register/")
        content = response.content.decode()
        self.assertContains(response, 'class="jalali-birthdate"', html=False)
        self.assertContains(response, 'class="jalali-birthdate__segment jalali-birthdate__segment--day"', html=False)
        self.assertContains(response, 'class="jalali-birthdate__segment jalali-birthdate__segment--month"', html=False)
        self.assertContains(response, 'class="jalali-birthdate__segment jalali-birthdate__segment--year"', html=False)
        self.assertLess(content.index('name="birthdate_2"'), content.index('name="birthdate_1"'))
        self.assertLess(content.index('name="birthdate_1"'), content.index('name="birthdate_0"'))
        self.assertContains(response, 'name="birthdate_2"', html=False)
        self.assertContains(response, 'name="birthdate_1"', html=False)
        self.assertContains(response, 'name="birthdate_0"', html=False)
        self.assertContains(response, 'name="birthdate_2" class="jalali-birthdate__control" inputmode="numeric"', html=False)
        self.assertContains(response, 'name="birthdate_0" class="jalali-birthdate__control" inputmode="numeric"', html=False)
        self.assertContains(response, "انتخاب ماه")
        self.assertContains(response, "آبان")
        self.assertNotContains(response, 'name="birthdate" type="date"', html=False)

        with translation.override("fa"):
            widgets = RegisterForm().fields["birthdate"].widget.widgets
        self.assertIsInstance(widgets[0], forms.TextInput)
        self.assertIsInstance(widgets[1], forms.Select)
        self.assertIsInstance(widgets[2], forms.TextInput)
        self.assertEqual([value for value, _label in widgets[1].choices if value], [str(month) for month in range(1, 13)])
        self.assertEqual(str(widgets[1].choices[0][1]), "انتخاب ماه")

    def test_persian_invalid_birthdate_error_stays_with_grouped_control(self):
        invalid = self.payload("invalid-grouped-jalali", birthdate_0="۱۴۰۰", birthdate_1="12", birthdate_2="۳۰")
        invalid.pop("birthdate")
        response = self.client.post("/fa/accounts/register/", invalid)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-jalali-birthdate')
        self.assertContains(response, 'aria-invalid="true"', html=False)
        self.assertContains(response, "تاریخ تولد معتبر")


@override_settings(SMS_ENABLED=True, SMS_OTP_LOGIN_ENABLED=True, SMS_BACKEND="accounts.sms.MemorySmsBackend")
class BirthdateSmsRegistrationTests(LanguageIsolationMixin, TestCase):
    def grant(self):
        session = self.client.session
        session["sms_signup_grant"] = {
            "phone": "+989121234599",
            "expires": (timezone.now().timestamp() + 600),
        }
        session.save()

    def test_persian_sms_completion_persists_canonical_gregorian_date(self):
        self.grant()
        response = self.client.post("/fa/accounts/register/sms/complete/", {
            "username": "sms-jalali",
            "email": "",
            "password1": "valid-password-123",
            "password2": "valid-password-123",
            "birthdate_0": "۱۳۷۴",
            "birthdate_1": "1",
            "birthdate_2": "۱",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get_user_model().objects.get(username="sms-jalali").profile.birthdate, date(1995, 3, 21))

    def test_persian_sms_completion_uses_the_shared_grouped_control(self):
        self.grant()
        response = self.client.get("/fa/accounts/register/sms/complete/")
        self.assertContains(response, 'class="jalali-birthdate"', html=False)
        self.assertContains(response, 'name="birthdate_2"', html=False)
        self.assertContains(response, 'name="birthdate_1"', html=False)
        self.assertContains(response, 'name="birthdate_0"', html=False)
        self.assertNotContains(response, 'name="birthdate" type="date"', html=False)


class BirthdateProfileTests(LanguageIsolationMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            "birthdate-user", email="birthdate@example.com", password="secret",
        )
        self.profile = Profile.objects.create(user=self.user, birthdate=date(1995, 3, 21))
        self.client.force_login(self.user)

    def fa_payload(self):
        return {
            "first_name": "", "last_name": "", "email": self.user.email,
            "phone": "", "birthdate_0": "۱۳۷۴", "birthdate_1": "1", "birthdate_2": "۱",
            "gender": "", "company_name": "", "company_tax_id": "",
        }

    def test_fa_edit_prepopulates_jalali_without_native_date_input(self):
        response = self.client.get("/fa/accounts/profile/")
        self.assertContains(response, 'class="jalali-birthdate"', html=False)
        self.assertContains(response, 'name="birthdate_0" value="1374"', html=False)
        self.assertContains(response, 'name="birthdate_1"', html=False)
        self.assertContains(response, 'value="1" selected', html=False)
        self.assertContains(response, 'data-jalali-birthdate-preview', html=False)
        self.assertContains(response, "۱ فروردین ۱۳۷۴")
        self.assertNotContains(response, 'name="birthdate" type="date"', html=False)

    def test_fa_round_trip_and_locale_switches_do_not_mutate_storage(self):
        response = self.client.post("/fa/accounts/profile/", self.fa_payload())
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.birthdate, date(1995, 3, 21))
        self.client.get("/en/accounts/profile/")
        self.client.get("/fa/accounts/profile/")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.birthdate, date(1995, 3, 21))

    def test_english_profile_keeps_gregorian_input(self):
        response = self.client.get("/en/accounts/profile/")
        self.assertContains(response, 'name="birthdate" value="1995-03-21"', html=False)
        self.assertContains(response, 'type="date"', html=False)

    def test_fa_edit_converts_changed_value_to_gregorian(self):
        payload = self.fa_payload()
        payload.update({"birthdate_0": "۱۳۷۵", "birthdate_1": "1", "birthdate_2": "۱"})
        self.client.post("/fa/accounts/profile/", payload)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.birthdate, date(1996, 3, 20))

    def test_fa_profile_validation_error_stays_with_the_grouped_control(self):
        payload = self.fa_payload()
        payload.update({"birthdate_0": "۱۴۰۰", "birthdate_1": "12", "birthdate_2": "۳۰"})
        response = self.client.post("/fa/accounts/profile/", payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-jalali-birthdate')
        self.assertContains(response, "تاریخ تولد معتبر")
