from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from dashboard.forms_commerce import CommerceSettingsForm
from dashboard.services_commerce import StaleCommercePolicy, update_commerce_settings
from orders.models import CommercePolicy, CommercePolicyAudit, DeliveryMethodPolicy, Order
from orders.pricing import calculate_pricing
from products.currency import Currency
from accounts.models import Address
from products.models import Product, ProductVariant, VariantPrice
from orders.selectors import CheckoutContext, CheckoutItem, CheckoutSummary, get_shipping_methods
from orders.services import CommercePolicyChanged, create_order


class CommercePricingTests(TestCase):
    def setUp(self):
        self.policy, _ = CommercePolicy.objects.get_or_create(singleton_key=1)
        defaults = {
            "standard": ("fixed", "150000", "3.50", True),
            "express": ("fixed", "300000", "8.00", False),
            "pickup": ("free", "0", "0", False),
        }
        for code, (mode, irt, usd, eligible) in defaults.items():
            DeliveryMethodPolicy.objects.update_or_create(code=code, defaults={
                "charge_mode": mode, "fixed_amount_irt": Decimal(irt), "fixed_amount_usd": Decimal(usd),
                "eligible_for_free_shipping_threshold": eligible,
            })

    def configure_threshold(self):
        self.policy.free_shipping_threshold_enabled = True
        self.policy.free_shipping_threshold_irt = Decimal("5000000")
        self.policy.free_shipping_threshold_usd = Decimal("50")
        self.policy.save()

    def test_fixed_threshold_eligibility_free_and_currency_independence(self):
        below = calculate_pricing(subtotal=Decimal("49.99"), currency=Currency.USD, delivery_method="standard")
        self.assertEqual(below.delivery_amount, Decimal("3.50"))
        self.configure_threshold()
        exact = calculate_pricing(subtotal=Decimal("50.00"), currency=Currency.USD, delivery_method="standard")
        above = calculate_pricing(subtotal=Decimal("50.01"), currency=Currency.USD, delivery_method="standard")
        express = calculate_pricing(subtotal=Decimal("100"), currency=Currency.USD, delivery_method="express")
        irt = calculate_pricing(subtotal=Decimal("1000"), currency=Currency.TOMAN, delivery_method="standard")
        self.assertTrue(exact.delivery_free_by_threshold)
        self.assertTrue(above.delivery_free_by_threshold)
        self.assertEqual(express.delivery_amount, Decimal("8.00"))
        self.assertEqual(irt.delivery_amount, Decimal("150000"))

    def test_threshold_disabled_and_pickup_free(self):
        result = calculate_pricing(subtotal=Decimal("999999"), currency=Currency.USD, delivery_method="pickup")
        self.assertTrue(result.delivery_is_free)
        self.assertFalse(result.delivery_free_by_threshold)
        self.assertEqual(result.delivery_amount, 0)

    def test_pay_on_delivery_is_explicit_and_excluded_from_payable_total(self):
        method = DeliveryMethodPolicy.objects.get(code="express")
        method.charge_mode = DeliveryMethodPolicy.ChargeMode.PAY_ON_DELIVERY
        method.save()
        result = calculate_pricing(subtotal=Decimal("40"), currency=Currency.USD, delivery_method="express")
        self.assertTrue(result.delivery_pay_on_delivery)
        self.assertFalse(result.delivery_is_free)
        self.assertEqual(result.delivery_amount, 0)
        self.assertEqual(result.total_payable_now, Decimal("40.00"))

    def test_tax_preserves_discounted_merchandise_subtotal_basis_and_rounding(self):
        disabled = calculate_pricing(subtotal=Decimal("19.99"), currency=Currency.USD, delivery_method="pickup")
        self.assertEqual(disabled.tax_amount, 0)
        self.policy.tax_enabled = True
        self.policy.tax_rate = Decimal("10")
        self.policy.save()
        usd = calculate_pricing(subtotal=Decimal("19.99"), currency=Currency.USD, delivery_method="pickup")
        irt = calculate_pricing(subtotal=Decimal("12345"), currency=Currency.TOMAN, delivery_method="pickup")
        self.assertEqual(usd.tax_amount, Decimal("2.00"))
        self.assertEqual(irt.tax_amount, Decimal("1235"))

    def test_rejects_unsupported_currency_and_method(self):
        with self.assertRaises(ValueError):
            calculate_pricing(subtotal=Decimal("1"), currency="EUR", delivery_method="standard")
        with self.assertRaises(ValueError):
            calculate_pricing(subtotal=Decimal("1"), currency=Currency.USD, delivery_method="tampered")

    def test_historical_order_snapshot_does_not_follow_policy(self):
        user = User.objects.create_user("buyer")
        order = Order.objects.create(user=user, full_name="Buyer", address="A", city="C", phone="1",
                                     currency="USD", shipping_cost=Decimal("3.50"), shipping_charge_mode="fixed",
                                     tax_rate_snapshot=Decimal("10"), tax_amount_snapshot=Decimal("2"), total_amount=Decimal("25.50"))
        self.policy.tax_rate = Decimal("20")
        self.policy.save()
        self.assertEqual(order.tax_amount, Decimal("2"))
        self.assertEqual(order.shipping_cost, Decimal("3.50"))
        self.assertEqual(order.total_amount, Decimal("25.50"))


class CommerceDashboardTests(TestCase):
    def setUp(self):
        self.policy = CommercePolicy.objects.get(singleton_key=1)
        self.methods = {m.code: m for m in DeliveryMethodPolicy.objects.all()}
        self.user = User.objects.create_user("manager", password="pw")
        self.url = reverse("dashboard:commerce:settings")

    def grant(self, *codenames):
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="orders", codename__in=codenames))

    def valid_data(self, **overrides):
        data = {"version": self.policy.version, "tax_enabled": "on", "tax_rate": "7.50",
                "threshold_enabled": "on", "threshold_irt": "5000000", "threshold_usd": "50"}
        for code, method in self.methods.items():
            data.update({f"{code}_mode": method.charge_mode, f"{code}_irt": str(method.fixed_amount_irt),
                         f"{code}_usd": str(method.fixed_amount_usd)})
            if method.eligible_for_free_shipping_threshold:
                data[f"{code}_eligible"] = "on"
        data.update(overrides)
        return data

    def test_anonymous_redirect_customer_and_unrelated_role_denied(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_view_and_change_permissions_are_explicit(self):
        self.grant("view_commercepolicy")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.valid_data()).status_code, 403)
        self.policy.refresh_from_db()
        self.assertFalse(self.policy.tax_enabled)

    def test_superuser_has_owner_access(self):
        owner = User.objects.create_superuser("commerce-owner", password="pw")
        self.client.force_login(owner)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_authorized_post_updates_atomically_and_audits_actor(self):
        self.grant("view_commercepolicy", "change_commercepolicy", "change_deliverymethodpolicy")
        self.client.force_login(self.user)
        response = self.client.post(self.url, self.valid_data())
        self.assertEqual(response.status_code, 302)
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.tax_enabled)
        self.assertEqual(self.policy.tax_rate, Decimal("7.50"))
        audit = CommercePolicyAudit.objects.filter(section="commerce").get()
        self.assertEqual(audit.actor, self.user)
        self.assertEqual(audit.before_values["tax_enabled"], False)
        self.assertEqual(audit.after_values["tax_enabled"], True)

    def test_invalid_and_stale_posts_do_not_audit_or_mutate(self):
        form = CommerceSettingsForm(self.valid_data(tax_rate="-1"), policy=self.policy, methods=self.methods)
        self.assertFalse(form.is_valid())
        self.assertEqual(CommercePolicyAudit.objects.count(), 0)
        data = self.valid_data()
        data["version"] = self.policy.version - 1
        form = CommerceSettingsForm(data, policy=self.policy, methods=self.methods)
        self.assertTrue(form.is_valid())
        with self.assertRaises(StaleCommercePolicy):
            update_commerce_settings(actor=self.user, cleaned_data=form.cleaned_data)
        self.assertEqual(CommercePolicyAudit.objects.count(), 0)

    def test_invalid_http_posts_never_mutate_or_audit(self):
        self.grant("view_commercepolicy", "change_commercepolicy", "change_deliverymethodpolicy")
        self.client.force_login(self.user)
        invalid_overrides = (
            {"tax_rate": "-1"},
            {"standard_mode": "invented"},
            {"express_usd": "not-a-number"},
            {"threshold_enabled": "on", "threshold_usd": ""},
            {"pickup_mode": "fixed", "pickup_irt": "", "pickup_usd": ""},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                response = self.client.post(self.url, self.valid_data(**overrides))
                self.assertEqual(response.status_code, 200)
                self.policy.refresh_from_db()
                self.assertFalse(self.policy.tax_enabled)
                self.assertEqual(CommercePolicyAudit.objects.count(), 0)


@override_settings(LOCAL_PAYMENT_SIMULATOR_ENABLED=True, MOCK_PAYMENT_SIMULATION_ENABLED=False)
class CommerceOrderIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("order-policy-buyer")
        self.address = Address.objects.create(user=self.user, first_name="Policy", last_name="Buyer", phone="123", country="IR", province="Tehran", city="Tehran", street_address="1 Way")
        self.product = Product.objects.create(name="Policy Tile", slug="policy-tile", sku="POLICY")
        self.variant = ProductVariant.objects.create(product=self.product, sku="POLICY-1", stock=20)
        VariantPrice.objects.create(variant=self.variant, currency=Currency.USD, amount=Decimal("100"))
        self.policy = CommercePolicy.objects.get(singleton_key=1)
        self.method = DeliveryMethodPolicy.objects.get(code="standard")

    def checkout(self):
        item = CheckoutItem(key=f"v_{self.variant.pk}", product=self.product, variant=self.variant, quantity=1, unit_price=Decimal("1"), subtotal=Decimal("1"))
        return CheckoutContext(items=[item], summary=CheckoutSummary(currency=Currency.USD, subtotal=Decimal("1"), shipping=Decimal("0"), discount=Decimal("0"), tax=Decimal("0"), total=Decimal("1")), cart_quantity=1, default_address=self.address, addresses=[self.address], shipping_methods=get_shipping_methods(Currency.USD), currency=Currency.USD, unavailable_variant_ids=[])

    def test_online_and_cod_snapshot_each_delivery_mode_without_conflating_dimensions(self):
        expected = {"fixed": Decimal("105.00"), "free": Decimal("100.00"), "pay_on_delivery": Decimal("100.00")}
        for payment in (Order.PaymentMethod.MOCK, Order.PaymentMethod.COD):
            for mode in expected:
                with self.subTest(payment=payment, mode=mode):
                    self.method.charge_mode = mode
                    self.method.fixed_amount_usd = Decimal("5")
                    self.method.save()
                    order = create_order(user=self.user, address=self.address,
                                         cleaned_data={"shipping_method": "standard", "payment_method": payment, "delivery_slot": ""},
                                         checkout=self.checkout())
                    self.assertEqual(order.total_amount, expected[mode])
                    self.assertEqual(order.shipping_pay_on_delivery, mode == "pay_on_delivery")
                    self.assertEqual(order.is_cash_on_delivery, payment == Order.PaymentMethod.COD)

    def test_tax_and_policy_version_are_snapshotted_and_stale_review_is_rejected(self):
        self.policy.tax_enabled = True
        self.policy.tax_rate = Decimal("10")
        self.policy.version = 7
        self.policy.save()
        order = create_order(user=self.user, address=self.address,
                             cleaned_data={"shipping_method": "pickup", "payment_method": "mock", "delivery_slot": ""},
                             checkout=self.checkout(), reviewed_policy_version=7)
        self.assertEqual(order.tax_amount_snapshot, Decimal("10.00"))
        self.assertEqual(order.tax_rate_snapshot, Decimal("10"))
        self.assertEqual(order.commerce_policy_version, 7)
        self.policy.version = 8
        self.policy.save()
        with self.assertRaises(CommercePolicyChanged):
            create_order(user=self.user, address=self.address,
                         cleaned_data={"shipping_method": "pickup", "payment_method": "mock", "delivery_slot": ""},
                         checkout=self.checkout(), reviewed_policy_version=7)

    def test_order_summary_renders_the_snapshotted_tax_rate(self):
        self.policy.tax_enabled = True
        self.policy.tax_rate = Decimal("10")
        self.policy.save()
        order = create_order(
            user=self.user,
            address=self.address,
            cleaned_data={"shipping_method": "pickup", "payment_method": "mock", "delivery_slot": ""},
            checkout=self.checkout(),
        )
        self.policy.tax_rate = Decimal("20")
        self.policy.save()
        self.client.force_login(self.user)
        response = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        self.assertContains(response, "Tax (10%)")
        self.assertNotContains(response, "Tax (20%)")
        with translation.override("fa"):
            response = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        self.assertContains(response, "مالیات (۱۰٪)")

    def test_client_supplied_financial_fields_are_ignored(self):
        self.policy.tax_enabled = True
        self.policy.tax_rate = Decimal("10")
        self.policy.save()
        self.method.charge_mode = DeliveryMethodPolicy.ChargeMode.FIXED
        self.method.fixed_amount_usd = Decimal("5")
        self.method.save()
        order = create_order(
            user=self.user,
            address=self.address,
            cleaned_data={
                "shipping_method": "standard",
                "payment_method": "mock",
                "delivery_slot": "",
                "subtotal": "0.01",
                "shipping_cost": "0.01",
                "tax": "0.01",
                "total": "0.03",
            },
            checkout=self.checkout(),
        )
        self.assertEqual(order.shipping_cost, Decimal("5.00"))
        self.assertEqual(order.tax_amount_snapshot, Decimal("10.00"))
        self.assertEqual(order.total_amount, Decimal("115.00"))

    def test_payment_gateway_receives_the_persisted_authoritative_total(self):
        self.method.charge_mode = DeliveryMethodPolicy.ChargeMode.FIXED
        self.method.fixed_amount_usd = Decimal("5")
        self.method.save()
        order = create_order(
            user=self.user,
            address=self.address,
            cleaned_data={"shipping_method": "standard", "payment_method": "mock", "delivery_slot": ""},
            checkout=self.checkout(),
        )
        gateway = Mock()
        gateway.request.return_value = "AUTH-1"
        gateway.payment_url.return_value = "https://payments.example/continue"
        self.client.force_login(self.user)
        with patch("orders.payment.get_payment_gateway_for_order", return_value=gateway):
            response = self.client.get(reverse("orders:payment_start", args=[order.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(gateway.request.call_args.kwargs["amount"], order.total_amount)
