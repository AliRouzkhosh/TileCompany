from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from accounts.models import Address
from products.catalog_domain import calculate_variant_combination_signature
from products.currency import Currency
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Product,
    ProductType,
    ProductTypeAttribute,
    ProductVariant,
    VariantAttributeValue,
    VariantPrice,
)


class StorefrontV5CCheckoutStepTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            "v5c-buyer", email="v5c@example.com", password="secret"
        )
        cls.address = Address.objects.create(
            user=cls.user,
            label="V5C home",
            first_name="Checkout",
            last_name="Buyer",
            phone="0000000000",
            country="Iran",
            province="Tehran",
            city="Tehran",
            street_address="Fixture street",
            postal_code="0000000000",
            is_default_shipping=True,
        )
        product_type = ProductType.objects.create(code="configurable-v5c", name="Configurable V5C")
        definition = AttributeDefinition.objects.create(
            code="memory_v5c", name="Memory", name_fa="حافظه", data_type="option"
        )
        binding = ProductTypeAttribute.objects.create(
            product_type=product_type,
            definition=definition,
            scope="variant",
            is_variant_dimension=True,
            is_required=True,
            show_on_storefront=True,
            show_in_specifications=True,
            include_in_purchase_snapshot=True,
            sort_order=1,
        )
        cls.variants = []
        for index in range(2):
            option = AttributeOption.objects.create(
                definition=definition,
                code=f"memory-{index}-v5c",
                name=f"{128 * (index + 1)} GB",
                name_fa=f"{128 * (index + 1)} گیگابایت",
            )
            product = Product.objects.create(
                product_type=product_type,
                name=f"Generic device {index + 1}",
                name_fa=f"دستگاه عمومی {index + 1}",
                slug=f"generic-device-{index + 1}-v5c",
                sku=f"DEVICE-V5C-{index + 1}",
                is_active=True,
            )
            variant = ProductVariant.objects.create(
                product=product,
                sku=f"DEVICE-V5C-{index + 1}-A",
                stock=12,
                price_unit=ProductVariant.PriceUnit.UNIT,
                is_active=True,
            )
            VariantAttributeValue.objects.create(variant=variant, binding=binding, option_value=option)
            variant.combination_signature = calculate_variant_combination_signature(variant)
            variant.save(update_fields=["combination_signature"])
            VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal("25.00"))
            VariantPrice.objects.create(variant=variant, currency=Currency.TOMAN, amount=Decimal("2500000"))
            cls.variants.append(variant)

    def setUp(self):
        self.client.force_login(self.user)

    def _set_checkout(self, *, currency=Currency.USD, lines=1, shipping=True, payment=True):
        session = self.client.session
        session["storefront_currency"] = currency
        session["cart_currency"] = currency
        session["cart"] = {f"v_{variant.pk}": index + 1 for index, variant in enumerate(self.variants[:lines])}
        if shipping:
            session["checkout_shipping"] = {
                "address_id": self.address.pk,
                "shipping_method": "express",
                "delivery_slot": "Morning",
            }
        else:
            session.pop("checkout_shipping", None)
        if payment:
            session["checkout_payment"] = {
                "payment_method": "mock" if currency == Currency.USD else "zarinpal",
                "coupon_code": "SAVE-V5C",
                "email_receipt_requested": True,
                "receipt_email": self.user.email,
            }
        else:
            session.pop("checkout_payment", None)
        session.save()

    def test_shipping_renders_native_selected_address_methods_slot_and_actions(self):
        self._set_checkout(payment=False)
        response = self.client.get(reverse("orders:checkout_shipping"))
        html = response.content.decode()
        self.assertContains(response, "Checkout Buyer")
        self.assertIn(f'id="checkout-address-{self.address.pk}"', html)
        self.assertRegex(html, rf'value="{self.address.pk}"[^>]*checked')
        self.assertIn('data-shipping-method-choice', html)
        self.assertRegex(html, r'value="express"[^>]*checked')
        self.assertContains(response, "$19.90")
        self.assertContains(response, "Morning")
        self.assertIn(reverse("orders:cart_detail"), html)
        self.assertIn('class="sf-button sf-button--primary"', html)

    def test_shipping_handles_zero_and_eight_addresses_without_template_queries(self):
        self._set_checkout(shipping=False, payment=False)
        Address.objects.filter(user=self.user).delete()
        empty = self.client.get(reverse("orders:checkout_shipping"))
        self.assertContains(empty, "You don\'t have any saved addresses yet.")
        self.assertContains(empty, 'data-address-empty', html=False)

        addresses = []
        for index in range(8):
            addresses.append(Address.objects.create(
                user=self.user,
                label=f"Address {index}",
                first_name="Buyer",
                last_name=str(index),
                phone=f"00000000{index:02d}",
                country="Iran",
                province="Tehran",
                city="Tehran",
                street_address=f"Street {index}",
                postal_code=f"100000000{index}",
                is_default_shipping=index == 0,
            ))
        response = self.client.get(reverse("orders:checkout_shipping"))
        self.assertEqual(response.content.decode().count("data-address-choice"), 8)
        self.assertContains(response, 'max-h-[32rem]', html=False)
        self.assertContains(response, "Street 7")

    def test_shipping_validation_is_local_and_exact_post_values_persist(self):
        self._set_checkout(shipping=False, payment=False)
        invalid = self.client.post(
            reverse("orders:checkout_shipping"),
            {"address_id": "", "shipping_method": "invalid", "delivery_slot": ""},
        )
        html = invalid.content.decode()
        self.assertEqual(invalid.status_code, 200)
        self.assertIn('id="address-errors"', html)
        self.assertIn('id="shipping-method-errors"', html)
        self.assertIn('role="alert"', html)

        valid = self.client.post(
            reverse("orders:checkout_shipping"),
            {"address_id": self.address.pk, "shipping_method": "pickup", "delivery_slot": "Afternoon"},
        )
        self.assertRedirects(valid, reverse("orders:checkout_payment"), fetch_redirect_response=False)
        self.assertEqual(
            self.client.session["checkout_shipping"],
            {"address_id": self.address.pk, "shipping_method": "pickup", "delivery_slot": "Afternoon"},
        )
        self.assertTrue(self.client.session.get("checkout_attempt_token"))

    def test_payment_methods_are_currency_truthful_and_optional_controls_are_secondary(self):
        self._set_checkout(currency=Currency.USD, payment=False)
        usd = self.client.get(reverse("orders:checkout_payment"))
        usd_html = usd.content.decode()
        self.assertIn('data-payment-method="mock"', usd_html)
        self.assertIn('data-payment-method="cod"', usd_html)
        self.assertNotIn('data-payment-method="zarinpal"', usd_html)
        self.assertIn('data-coupon-disclosure', usd_html)
        self.assertIn('data-email-receipt-option', usd_html)

        self._set_checkout(currency=Currency.TOMAN, payment=False)
        with translation.override("fa"):
            toman_html = self.client.get(reverse("orders:checkout_payment")).content.decode()
        self.assertIn('data-payment-method="zarinpal"', toman_html)
        self.assertIn('data-payment-method="cod"', toman_html)
        self.assertNotIn('data-payment-method="mock"', toman_html)

    def test_payment_validation_and_exact_values_persist_to_review(self):
        self._set_checkout(payment=False)
        invalid = self.client.post(reverse("orders:checkout_payment"), {"payment_method": "invalid"})
        self.assertEqual(invalid.status_code, 200)
        self.assertIn('id="payment-method-errors"', invalid.content.decode())

        valid = self.client.post(
            reverse("orders:checkout_payment"),
            {"payment_method": "cod", "coupon_code": "PROMO-EXACT", "email_receipt_requested": "on"},
        )
        self.assertRedirects(valid, reverse("orders:checkout_review"), fetch_redirect_response=False)
        self.assertEqual(
            self.client.session["checkout_payment"],
            {
                "payment_method": "cod",
                "coupon_code": "PROMO-EXACT",
                "email_receipt_requested": True,
                "receipt_email": self.user.email,
            },
        )

    def test_review_focuses_on_shipping_payment_and_keeps_attempt_token(self):
        self._set_checkout()
        response = self.client.get(reverse("orders:checkout_review"))
        html = response.content.decode()
        main = html.split('data-checkout-content', 1)[1].split('data-checkout-summary-column', 1)[0]
        self.assertIn('data-review-shipping', main)
        self.assertIn('data-review-payment', main)
        self.assertIn("Express delivery", main)
        self.assertIn("Development payment", main)
        self.assertIn("Morning", main)
        self.assertNotIn('data-checkout-line', main)
        self.assertEqual(html.count('data-checkout-summary>'), 1)
        self.assertIn('name="checkout_attempt_token"', html)
        self.assertIn('class="sf-button sf-button--primary"', main)
        self.assertIn(reverse("orders:checkout_payment"), main)

    def test_review_post_keeps_isolated_order_creation_boundary(self):
        self._set_checkout()
        self.client.get(reverse("orders:checkout_review"))
        token = self.client.session["checkout_attempt_token"]
        fake_order = SimpleNamespace(id=991, requires_online_payment=True)
        with patch("orders.checkout.create_order", return_value=fake_order) as create, patch(
            "orders.checkout.detach_created_order_cart"
        ) as detach:
            response = self.client.post(
                reverse("orders:checkout_review"),
                {"checkout_attempt_token": token},
            )
        self.assertRedirects(
            response,
            reverse("orders:payment_start", args=[fake_order.id]),
            fetch_redirect_response=False,
        )
        cleaned = create.call_args.kwargs["cleaned_data"]
        self.assertEqual(cleaned["address_id"], self.address.pk)
        self.assertEqual(cleaned["shipping_method"], "express")
        self.assertEqual(cleaned["payment_method"], "mock")
        self.assertEqual(str(create.call_args.kwargs["checkout_attempt_token"]), token)
        detach.assert_called_once()

    def test_native_associations_responsive_actions_and_no_js_authority(self):
        self._set_checkout()
        root = Path(settings.BASE_DIR)
        paths = (
            "orders/templates/orders/shipping.html",
            "orders/templates/orders/payment.html",
            "orders/templates/orders/review.html",
            "orders/templates/orders/components/shipping_address_card.html",
            "orders/templates/orders/components/shipping_method_card.html",
            "orders/templates/orders/components/payment_method_card.html",
            "orders/templates/orders/components/order_review.html",
        )
        source = "\n".join((root / path).read_text(encoding="utf-8") for path in paths)
        css = (root / "theme/static_src/src/styles.css").read_text(encoding="utf-8")
        self.assertIn('for="checkout-address-', source)
        self.assertIn('for="shipping-method-', source)
        self.assertIn('for="{{ radio.id_for_label }}"', source)
        self.assertIn("checkout-actions", source)
        self.assertIn("flex-direction: column-reverse", css)
        self.assertIn("@media (min-width: 40rem)", css)
        self.assertNotIn("<script", source)

    def test_templates_are_generic_and_perform_no_orm_traversal(self):
        root = Path(settings.BASE_DIR)
        paths = list((root / "orders/templates/orders").glob("*.html")) + list(
            (root / "orders/templates/orders/components").glob("*.html")
        )
        source = "\n".join(path.read_text(encoding="utf-8").lower() for path in paths if path.name in {
            "shipping.html", "payment.html", "review.html", "shipping_address_card.html",
            "shipping_method_card.html", "payment_method_card.html", "order_review.html",
        })
        self.assertNotRegex(source, r"\.(?:all|filter|children|variants|attribute_values)\b")
        self.assertNotRegex(source, r"(?:product_type|definition_code)\s*(?:==|===)")
        self.assertNotRegex(source, r"{%\s*if[^%]*(?:brand|material|design|usage|color|size|finish|ram|storage|cpu|gpu)")

    def test_query_counts_remain_at_v5b_baseline_and_do_not_grow_with_addresses(self):
        self._set_checkout()
        # Keep the V5B capability-eligibility allowance while proving that
        # extra customer addresses do not increase the request budget.
        expected = {"shipping": 24, "payment": 23, "review": 24}
        results = {}
        for step in expected:
            self.client.get(reverse(f"orders:checkout_{step}"))
            with CaptureQueriesContext(connection) as queries:
                self.client.get(reverse(f"orders:checkout_{step}"))
            results[(step, 1)] = len(queries)
            self.assertLessEqual(len(queries), expected[step])

        for index in range(7):
            Address.objects.create(
                user=self.user, label=f"Extra {index}", first_name="Extra", last_name=str(index),
                phone="0", country="Iran", province="Tehran", city="Tehran",
                street_address=f"Extra street {index}", postal_code=str(index),
            )
        for step in expected:
            with CaptureQueriesContext(connection) as queries:
                self.client.get(reverse(f"orders:checkout_{step}"))
            results[(step, 8)] = len(queries)
            self.assertEqual(results[(step, 1)], results[(step, 8)])

    def test_persian_step_copy_is_localized(self):
        self._set_checkout(currency=Currency.TOMAN)
        with translation.override("fa"):
            shipping = self.client.get(reverse("orders:checkout_shipping"))
            payment = self.client.get(reverse("orders:checkout_payment"))
            review = self.client.get(reverse("orders:checkout_review"))
        self.assertContains(shipping, "نشانی تحویل")
        self.assertContains(payment, "روش پرداخت")
        self.assertContains(review, "سفارش خود را بررسی کنید")
