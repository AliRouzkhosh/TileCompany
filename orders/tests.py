from django.contrib.auth import get_user_model
from django.contrib.messages import constants as message_constants, get_messages
from django.test import override_settings
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import translation
from decimal import Decimal
from pathlib import Path

from orders.models import Order, OrderItem
from orders.purchase_presentation import purchase_history_item_queryset
from orders.services import create_order, reserve_variant_stock, restore_stock_for_order
from orders.cart import _build_cart
from orders.selectors import CheckoutContext, CheckoutItem, CheckoutSummary, get_customer_order_history, get_shipping_methods
from products.models import Brand, Color, Finish, Material, Product, ProductVariant, Size, VariantPrice
from products.currency import Currency
from products.commerce_presentation import prepare_commerce_line_presentations
from dashboard.services import approve_refund, process_refund, reject_refund
from dashboard.selectors.orders import (
    get_fulfillment_queue,
    get_refund_processing_queue,
    get_refund_review_queue,
)
from orders.payment import DevelopmentZarinPalGateway, MockGateway, get_payment_gateway_for_order


class OrderHtmxNavigationContractTests(TestCase):
    def test_browse_products_opt_out_and_pagination_marks_replaced_links(self):
        source = (Path(__file__).resolve().parent / "templates" / "orders" / "components" / "orders_workspace.html").read_text(encoding="utf-8")

        self.assertIn('href="{% url \'products:product_list\' %}" hx-boost="false"', source)
        self.assertIn("data-pagination-scroll-target", source)
        self.assertIn("data-pagination-link", source)


class VariantOnlyCartAndInventoryTests(TestCase):
    def setUp(self):
        translation.activate("en")
        self.product = Product.objects.create(
            name="Pearl Beige", slug="pearl-beige", sku="PEARL-BEIGE", is_active=True
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="PB-60120-MAT", stock=2, is_active=True
        )
        VariantPrice.objects.create(variant=self.variant, currency=Currency.USD, amount=Decimal("30.00"))
        VariantPrice.objects.create(variant=self.variant, currency=Currency.TOMAN, amount=Decimal("3000000"))

    def test_cart_rejects_out_of_stock_variant_and_uses_variant_key(self):
        self.variant.stock = 0
        self.variant.save(update_fields=["stock"])
        response = self.client.post(
            reverse("orders:cart_add_variant"), {"variant_id": self.variant.pk, "quantity": 1}
        )
        self.assertRedirects(response, reverse("products:product_list"))
        self.assertNotIn(f"v_{self.variant.pk}", self.client.session.get("cart", {}))

        self.variant.stock = 2
        self.variant.save(update_fields=["stock"])
        self.client.post(
            reverse("orders:cart_add_variant"), {"variant_id": self.variant.pk, "quantity": 5}
        )
        self.assertEqual(self.client.session["cart"], {f"v_{self.variant.pk}": 2})

    def test_variant_reservation_never_makes_stock_negative(self):
        reserve_variant_stock(variant_id=self.variant.pk, quantity=2)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 0)

        with self.assertRaises(ValueError):
            reserve_variant_stock(variant_id=self.variant.pk, quantity=1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 0)

    def test_cart_uses_explicit_currency_without_legacy_fallback(self):
        response = self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant.pk, "quantity": 1})
        self.assertEqual(response.status_code, 302)
        cart = _build_cart(self.client)
        self.assertEqual(cart["currency"], Currency.USD)
        self.assertEqual(cart["items"][0]["unit_price"], Decimal("30.00"))

    def test_irt_cart_rejects_variant_without_irt_price(self):
        self.variant.prices.filter(currency=Currency.TOMAN).delete()
        session = self.client.session
        session["storefront_currency"] = Currency.TOMAN
        session.save()
        with translation.override("fa"):
            self.client.post(reverse("orders:cart_add_variant"), {"variant_id": self.variant.pk, "quantity": 1})
            self.assertEqual(self.client.session.get("cart"), {})

    def test_persian_cart_uses_localized_catalog_attributes_and_guidance(self):
        brand = Brand.objects.create(name="Ariana", name_fa="آریانا", slug="ariana")
        material = Material.objects.create(name="Porcelain", name_fa="پرسلان", slug="porcelain")
        color = Color.objects.create(name="Blue", name_fa="آبی", slug="blue")
        finish = Finish.objects.create(name="Natural", name_fa="طبیعی", slug="natural")
        product = Product.objects.create(
            name="Venice Terrazzo",
            name_fa="ترازوی ونیز",
            slug="venice-terrazzo",
            sku="VENICE-TERRAZZO",
            brand=brand,
            series_name="Venice Terrazzo",
            series_name_fa="ترازوی ونیز",
            is_active=True,
        )
        product.materials.add(material)
        variant = ProductVariant.objects.create(
            product=product,
            sku="VENICE-BLUE-NATURAL",
            color=color,
            finish=finish,
            stock=3,
            is_active=True,
        )
        VariantPrice.objects.create(variant=variant, currency=Currency.TOMAN, amount=Decimal("5000000"))

        with translation.override("fa"):
            self.client.post(reverse("orders:cart_add_variant"), {"variant_id": variant.pk, "quantity": 1})
            response = self.client.get(reverse("orders:cart_detail"))

        self.assertContains(response, "آریانا")
        self.assertContains(response, "ترازوی ونیز")
        self.assertContains(response, "پرسلان")
        self.assertContains(response, "آبی")
        self.assertContains(response, "طبیعی")
        self.assertContains(response, "محصولات شما پس از ثبت سفارش رزرو می‌شوند.")
        self.assertContains(response, "سفارش‌های در انتظار باید ظرف")
        self.assertContains(response, "24 ساعت")
        self.assertContains(response, "موجودی رزروشده به‌طور خودکار آزاد می‌شود.")
        self.assertContains(response, "هزینه ارسال و زمان تقریبی تحویل هنگام تسویه‌حساب محاسبه می‌شود.")

    def test_persian_checkout_summary_uses_localized_variant_labels(self):
        color = Color.objects.create(name="Blue", name_fa="آبی", slug="blue-review")
        finish = Finish.objects.create(name="Natural", name_fa="طبیعی", slug="natural-review")
        size = Size.objects.create(name="60x60cm", name_fa="60×60 سانتی‌متر", slug="60x60-review")
        product = Product.objects.create(
            name="Venice Terrazzo", name_fa="ترازو ونیز", slug="venice-review", sku="VENICE-REVIEW"
        )
        variant = ProductVariant.objects.create(
            product=product, sku="VENICE-REVIEW-BLUE", color=color, finish=finish, size=size, stock=2
        )
        checkout = CheckoutContext(
            items=[CheckoutItem(key="v_review", product=product, variant=variant, quantity=1, unit_price=Decimal("5000000"), subtotal=Decimal("5000000"))],
            summary=CheckoutSummary(currency=Currency.TOMAN, subtotal=Decimal("5000000"), shipping=Decimal("0"), discount=Decimal("0"), tax=Decimal("0"), total=Decimal("5000000")),
            cart_quantity=1,
            default_address=None,
            addresses=[],
            shipping_methods=[],
            currency=Currency.TOMAN,
            unavailable_variant_ids=[],
        )
        checkout.items[0].presentation = prepare_commerce_line_presentations(
            checkout.items, currency=Currency.TOMAN, language="fa"
        )[variant.pk]

        with translation.override("fa"):
            html = render_to_string("orders/components/checkout_summary.html", {"checkout": checkout})

        self.assertIn("60×60 سانتی‌متر", html)
        self.assertIn("آبی", html)
        self.assertIn("طبیعی", html)
        self.assertNotIn("Blue", html)
        self.assertNotIn("Natural", html)

    def test_restoration_returns_stock_to_variant_only(self):
        user = get_user_model().objects.create_user(username="buyer", password="secret")
        order = Order.objects.create(
            user=user,
            full_name="Buyer", address="1 Tile Street", city="Tehran", phone="123",
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            variant=self.variant,
            product_name=self.product.name,
            unit_price=100,
            quantity=2,
        )
        self.variant.stock = 0
        self.variant.save(update_fields=["stock"])

        restore_stock_for_order(order)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 2)

    def test_legacy_snapshot_localizes_only_matching_prepared_live_labels(self):
        color = Color.objects.create(name="Blue", name_fa="آبی", slug="blue-history")
        finish = Finish.objects.create(name="Natural", name_fa="طبیعی", slug="natural-history")
        size = Size.objects.create(name="60x60cm", name_fa="60×60 سانتی‌متر", slug="60x60-history")
        material = Material.objects.create(name="Porcelain", name_fa="پرسلان", slug="porcelain-history")
        product = Product.objects.create(name="History Tile", name_fa="کاشی تاریخی", slug="history-tile", sku="HISTORY-TILE")
        product.materials.add(material)
        variant = ProductVariant.objects.create(product=product, sku="HISTORY-60", color=color, finish=finish, size=size)
        order = Order.objects.create(user=get_user_model().objects.create_user("history-buyer"), full_name="Buyer", address="1", city="Tehran", phone="123")
        item = OrderItem.objects.create(
            order=order,
            product=product,
            variant=variant,
            product_name="History Tile (HISTORY-60)",
            unit_price=Decimal("1"),
            quantity=1,
            variant_snapshot={"size": "60x60cm", "material": "Porcelain", "finish": "Natural", "color": "Blue", "thickness": ""},
        )

        with translation.override("fa"):
            self.assertEqual(item.display_product_name, product.name_fa)
            self.assertEqual(
                item.display_specification_chips,
                ["Porcelain", size.name_fa, finish.name_fa, color.name_fa],
            )
            self.client.force_login(order.user)
            response = self.client.get(reverse("orders:order_detail", args=[order.pk]))
            self.assertContains(response, "سفارش شمارهٔ")
            self.assertContains(response, product.name_fa)
            self.assertContains(response, material.name_fa)
            self.assertContains(response, color.name_fa)

        color.name = "Changed Blue"
        color.save(update_fields=["name"])
        product.name = "Changed History Tile"
        product.save(update_fields=["name"])
        prepared_item = purchase_history_item_queryset().get(pk=item.pk)
        with translation.override("fa"):
            self.assertEqual(prepared_item.display_product_name, product.name_fa)
            # The current Color no longer matches frozen historical identity,
            # so presentation falls back to the immutable snapshot string.
            self.assertEqual(prepared_item.display_specification_chips[-1], "Blue")

    def _refund_order(self, *, status, requested_stage):
        user = get_user_model().objects.create_user(username=f"buyer-{status}", password="secret")
        order = Order.objects.create(
            user=user,
            full_name="Buyer",
            address="1 Tile Street",
            city="Tehran",
            phone="123",
            status=status,
            is_paid=True,
            refund_status=Order.RefundStatus.REQUESTED,
            refund_requested_stage=requested_stage,
        )
        OrderItem.objects.create(
            order=order, product=self.product, variant=self.variant,
            product_name=self.product.name, unit_price=100, quantity=2,
        )
        self.variant.stock = 0
        self.variant.save(update_fields=["stock"])
        return order, user

    def test_pre_shipment_approval_releases_inventory_once_and_blocks_shipping(self):
        order, user = self._refund_order(
            status=Order.Status.CONFIRMED, requested_stage=Order.Status.CONFIRMED
        )
        self.assertFalse(order.can_ship)
        self.assertNotIn(order, get_fulfillment_queue())
        self.assertIn(order, get_refund_review_queue())

        result = approve_refund(order, user, "Approved")
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertTrue(result.inventory_restored)
        self.assertEqual(result.restored_quantity, 2)
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(order.refund_status, Order.RefundStatus.APPROVED)
        self.assertIsNotNone(order.inventory_restored_at)
        self.assertEqual(self.variant.stock, 2)
        self.assertIn(order, get_refund_processing_queue())

        processed = process_refund(order, user)
        self.variant.refresh_from_db()
        order.refresh_from_db()
        self.assertTrue(processed.inventory_already_restored)
        self.assertEqual(self.variant.stock, 2)
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(order.refund_status, Order.RefundStatus.PROCESSED)

    def test_delivered_refund_restores_only_when_processed(self):
        order, user = self._refund_order(
            status=Order.Status.DELIVERED, requested_stage=Order.Status.DELIVERED
        )
        approval = approve_refund(order, user, "Approved")
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertFalse(approval.inventory_restored)
        self.assertEqual(order.status, Order.Status.DELIVERED)
        self.assertEqual(self.variant.stock, 0)

        processed = process_refund(order, user)
        order.refresh_from_db()
        self.variant.refresh_from_db()
        self.assertTrue(processed.inventory_restored)
        self.assertEqual(processed.restored_quantity, 2)
        self.assertEqual(order.status, Order.Status.DELIVERED)
        self.assertEqual(self.variant.stock, 2)

    def test_rejected_refund_allows_confirmed_order_to_ship_again(self):
        order, user = self._refund_order(
            status=Order.Status.CONFIRMED, requested_stage=Order.Status.CONFIRMED
        )
        reject_refund(order, user, "Rejected")
        order.refresh_from_db()
        self.assertTrue(order.can_ship)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 0)


class OrderCurrencyAndUnitPriceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="currency-buyer", password="secret"
        )
        self.product = Product.objects.create(
            name="Snapshot Tile", slug="snapshot-tile", sku="CAT-SNAPSHOT"
        )
        self.variant = ProductVariant.objects.create(
            product=self.product, sku="SNAPSHOT-TILE-A", stock=5
        )
        VariantPrice.objects.create(variant=self.variant, currency=Currency.USD, amount=Decimal("30.00"))
        VariantPrice.objects.create(variant=self.variant, currency=Currency.TOMAN, amount=Decimal("3000000"))

    def test_order_has_one_explicit_currency(self):
        order = Order.objects.create(
            user=self.user,
            full_name="Buyer",
            address="1 Tile Street",
            city="Tehran",
            phone="123",
            currency=Currency.TOMAN,
        )

        self.assertEqual(order.currency, Currency.TOMAN)

    def test_unit_price_is_an_immutable_purchase_time_snapshot(self):
        order = Order.objects.create(
            user=self.user,
            full_name="Buyer",
            address="1 Tile Street",
            city="Tehran",
            phone="123",
            currency=Currency.USD,
        )
        item = OrderItem.objects.create(
            order=order,
            product=self.product,
            variant=self.variant,
            product_name=self.product.name,
            unit_price=Decimal("30.00"),
            quantity=3,
        )

        self.assertEqual(item.unit_price, Decimal("30.00"))
        self.assertEqual(item.subtotal, Decimal("90.00"))

    def test_order_creation_reresolves_variant_price_and_snapshots_currency(self):
        from accounts.models import Address
        address = Address.objects.create(user=self.user, first_name="Buyer", last_name="One", phone="123", country="IR", province="Tehran", city="Tehran", street_address="1 Tile Street")
        checkout = CheckoutContext(
            items=[CheckoutItem(key="v_1", product=self.product, variant=self.variant, quantity=2, unit_price=Decimal("1.00"), subtotal=Decimal("2.00"))],
            summary=CheckoutSummary(currency=Currency.USD, subtotal=Decimal("2.00"), shipping=Decimal("0.00"), discount=Decimal("0.00"), tax=Decimal("0.00"), total=Decimal("2.00")),
            cart_quantity=2, default_address=address, addresses=[address], shipping_methods=get_shipping_methods(Currency.USD), currency=Currency.USD, unavailable_variant_ids=[],
        )
        order = create_order(user=self.user, address=address, cleaned_data={"shipping_method": "express", "payment_method": "mock", "delivery_slot": ""}, checkout=checkout)
        item = order.items.get()
        self.assertEqual(order.currency, Currency.USD)
        self.assertEqual(item.unit_price, Decimal("30.00"))
        self.assertEqual(order.shipping_cost, Decimal("19.90"))
        self.assertEqual(order.total_amount, Decimal("79.90"))

    def test_irt_order_creation_snapshots_the_independent_toman_price(self):
        from accounts.models import Address
        address = Address.objects.create(user=self.user, first_name="Buyer", last_name="One", phone="123", country="IR", province="Tehran", city="Tehran", street_address="1 Tile Street")
        checkout = CheckoutContext(
            items=[CheckoutItem(key="v_1", product=self.product, variant=self.variant, quantity=2, unit_price=Decimal("1.00"), subtotal=Decimal("2.00"))],
            summary=CheckoutSummary(currency=Currency.TOMAN, subtotal=Decimal("2.00"), shipping=Decimal("0.00"), discount=Decimal("0.00"), tax=Decimal("0.00"), total=Decimal("2.00")),
            cart_quantity=2, default_address=address, addresses=[address], shipping_methods=get_shipping_methods(Currency.TOMAN), currency=Currency.TOMAN, unavailable_variant_ids=[],
        )
        order = create_order(user=self.user, address=address, cleaned_data={"shipping_method": "express", "payment_method": "zarinpal", "delivery_slot": ""}, checkout=checkout)
        self.assertEqual(order.currency, Currency.TOMAN)
        self.assertEqual(order.items.get().unit_price, Decimal("3000000"))

    def test_shipping_values_are_independent_by_currency(self):
        self.assertEqual(next(method.amount for method in get_shipping_methods(Currency.USD) if method.code == "express"), Decimal("19.90"))
        self.assertEqual(next(method.amount for method in get_shipping_methods(Currency.TOMAN) if method.code == "express"), Decimal("1500000"))

    def test_usd_order_cannot_use_zarinpal_but_can_use_mock_gateway(self):
        order = Order.objects.create(user=self.user, full_name="Buyer", address="1", city="Tehran", phone="123", currency=Currency.USD, payment_method="zarinpal")
        with self.assertRaises(ValueError):
            get_payment_gateway_for_order(order)
        order.payment_method = "mock"
        self.assertIsInstance(get_payment_gateway_for_order(order), MockGateway)

    @override_settings(ZARINPAL_MERCHANT_ID="", ZARINPAL_DEVELOPMENT_AUTO_SUCCESS=False)
    def test_unconfigured_zarinpal_redirects_with_a_safe_message(self):
        order = Order.objects.create(
            user=self.user,
            full_name="Buyer",
            address="1",
            city="Tehran",
            phone="+989022813770",
            currency=Currency.TOMAN,
            payment_method=Order.PaymentMethod.ZARINPAL,
            total_amount=Decimal("3000000"),
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("orders:payment_start", args=[order.pk]))

        self.assertRedirects(response, reverse("orders:order_detail", args=[order.pk]))
        message = next(iter(get_messages(response.wsgi_request)))
        self.assertEqual(message.level, message_constants.ERROR)
        self.assertTrue(str(message))

    @override_settings(ZARINPAL_MERCHANT_ID="", ZARINPAL_DEVELOPMENT_AUTO_SUCCESS=True)
    def test_development_zarinpal_payment_uses_the_outcome_simulator_without_a_gateway_request(self):
        order = Order.objects.create(
            user=self.user,
            full_name="Buyer",
            address="1",
            city="Tehran",
            phone="+989022813770",
            currency=Currency.TOMAN,
            payment_method=Order.PaymentMethod.ZARINPAL,
            total_amount=Decimal("3000000"),
        )
        self.assertIsInstance(get_payment_gateway_for_order(order), DevelopmentZarinPalGateway)
        self.client.force_login(self.user)

        response = self.client.get(reverse("orders:payment_start", args=[order.pk]))

        self.assertRedirects(response, reverse("orders:mock_payment", args=[order.pk]))
        order.refresh_from_db()
        self.assertFalse(order.is_paid)
        self.assertEqual(order.payment_reference, "DEV_ZARINPAL_AUTHORITY")
        simulator = self.client.get(reverse("orders:mock_payment", args=[order.pk]))
        self.assertContains(simulator, "ZarinPal development simulator")


class CustomerOrderListTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="orders-buyer", password="secret")
        self.other_user = get_user_model().objects.create_user(username="other-orders-buyer", password="secret")
        self.client.force_login(self.user)

    def order(self, *, status=Order.Status.CONFIRMED, payment_method=Order.PaymentMethod.COD, paid=False, number=1, user=None):
        order = Order.objects.create(
            user=user or self.user,
            user_order_number=number,
            full_name="Buyer",
            address="1 Example Street",
            city="Tehran",
            phone="123",
            status=status,
            payment_method=payment_method,
            is_paid=paid,
            total_amount=Decimal("42.00"),
        )
        OrderItem.objects.create(
            order=order,
            product_name="Snapshot Tile",
            unit_price=Decimal("42.00"),
            quantity=1,
            variant_snapshot={"sku": "SNAPSHOT-TILE", "size": "60x60 cm"},
        )
        return order

    def generic_snapshot(self, *, product_type, name, configuration=()):
        """A minimal generic v2 snapshot, intentionally independent of live types."""

        return {
            "version": 2,
            "product": {
                "id": 1,
                "product_type": {
                    "code": product_type.lower(),
                    "label_default": product_type,
                    "label_fa": product_type,
                },
                "sku": f"HIST-{product_type.upper()}",
                "name_default": name,
                "name_fa": name,
            },
            "variant": {
                "id": 1,
                "sku": f"HIST-{product_type.upper()}-VARIANT",
                "combination_signature": "__base__",
                "selling_unit": {
                    "code": "unit",
                    "price_label_default": "Per Unit",
                    "price_label_fa": "Per Unit",
                    "singular_noun_default": "item",
                    "singular_noun_fa": "item",
                    "plural_noun_default": "items",
                    "plural_noun_fa": "items",
                },
            },
            "attributes": {
                "product": [],
                "variant": [
                    {
                        "definition_code": f"config-{index}",
                        "label_default": "Configuration",
                        "label_fa": "Configuration",
                        "scope": "variant",
                        "data_type": "text",
                        "unit": None,
                        "is_dimension": False,
                        "sort_order": index,
                        "values": [{
                            "option_code": None,
                            "raw_value": value,
                            "display_default": value,
                            "display_fa": value,
                        }],
                    }
                    for index, value in enumerate(configuration, start=1)
                ],
            },
            "operational": {},
        }

    def set_snapshot_item(self, order, *, product_name, snapshot, product=None, quantity=1):
        item = order.items.get()
        item.product = product
        item.product_name = product_name
        item.variant_snapshot = snapshot
        item.quantity = quantity
        item.save()
        return item

    def test_orders_list_uses_the_account_shell_and_active_orders_destination(self):
        response = self.client.get(reverse("orders:order_list"))
        self.assertContains(response, "account-shell")
        self.assertContains(response, "data-account-nav-selector")
        self.assertNotContains(response, "data-account-nav-auto-reveal")
        self.assertContains(response, 'href="%s"' % reverse("orders:order_list"))
        self.assertContains(response, 'aria-current="page"')

    def test_status_filters_are_server_authoritative_and_preserve_ownership(self):
        current = self.order(status=Order.Status.CONFIRMED, number=10)
        delivered = self.order(status=Order.Status.DELIVERED, paid=True, number=11)
        cancelled = self.order(status=Order.Status.CANCELLED, number=12)
        other = self.order(status=Order.Status.CONFIRMED, number=99, user=self.other_user)

        for status, included, excluded in (("current", current, delivered), ("delivered", delivered, current), ("cancelled", cancelled, current)):
            response = self.client.get(reverse("orders:order_list"), {"status": status})
            self.assertContains(response, f"Order #{included.user_order_number}")
            self.assertNotContains(response, f"Order #{excluded.user_order_number}")
            self.assertNotContains(response, f"Order #{other.user_order_number}")

        invalid = self.client.get(reverse("orders:order_list"), {"status": "unknown"})
        self.assertContains(invalid, "Order #10")
        self.assertContains(invalid, 'status=current')

    def test_payment_required_is_order_first_and_cod_is_not_misclassified(self):
        retryable = self.order(status=Order.Status.PENDING, payment_method=Order.PaymentMethod.MOCK, number=20)
        cod = self.order(status=Order.Status.CONFIRMED, payment_method=Order.PaymentMethod.COD, number=21)
        response = self.client.get(reverse("orders:order_list"), {"status": "current"})

        self.assertContains(response, "Payment required")
        self.assertContains(response, "Review and pay")
        self.assertContains(response, reverse("orders:order_detail", args=[retryable.pk]))
        self.assertNotContains(response, reverse("orders:payment_start", args=[retryable.pk]))
        self.assertContains(response, "Confirmed")
        self.assertContains(response, "Payment due on delivery")
        self.assertEqual(response.content.decode().count("account-order-card__status--payment-required"), 1)
        self.assertContains(response, f"Order #{cod.user_order_number}")

    def test_all_order_cards_use_the_same_structural_zones_and_clamp_hooks(self):
        self.order(status=Order.Status.PENDING, payment_method=Order.PaymentMethod.MOCK, number=40)
        self.order(status=Order.Status.CONFIRMED, payment_method=Order.PaymentMethod.COD, number=41)
        response = self.client.get(reverse("orders:order_list"), {"status": "current"})
        content = response.content.decode()

        self.assertEqual(content.count('class="account-order-card__topline"'), 2)
        self.assertEqual(content.count('class="account-order-card__identity"'), 2)
        self.assertEqual(content.count('class="account-order-card__items"'), 2)
        self.assertEqual(content.count('class="account-order-card__footer"'), 2)
        self.assertEqual(content.count('<p class="account-order-card__secondary-state'), 2)
        self.assertEqual(content.count('class="account-overview__quiet-action"'), 2)

        styles = (Path(__file__).resolve().parents[1] / "theme" / "static_src" / "src" / "styles.css").read_text()
        self.assertIn("grid-template-rows: auto minmax(2.75rem, auto) minmax(5.75rem, auto) auto", styles)
        self.assertIn("-webkit-line-clamp: 1", styles)
        self.assertIn(".account-order-card__items { display: grid; min-height: 5.75rem", styles)
        self.assertIn("grid-template-columns: 3rem minmax(0, 1fr) auto", styles)
        self.assertIn(".account-order-card__item-thumbnail img { width: 100%; height: 100%; object-fit: contain; }", styles)

    def test_preview_limit_pagination_and_status_query_count_are_bounded(self):
        for number in range(1, 13):
            order = self.order(number=number)
            OrderItem.objects.create(order=order, product_name="Second", unit_price=Decimal("1"), quantity=1, variant_snapshot={"sku": "SECOND", "size": "30x30 cm"})
            OrderItem.objects.create(order=order, product_name="Third", unit_price=Decimal("1"), quantity=1, variant_snapshot={"sku": "THIRD", "size": "30x30 cm"})
            OrderItem.objects.create(order=order, product_name="Fourth", unit_price=Decimal("1"), quantity=1, variant_snapshot={"sku": "FOURTH", "size": "30x30 cm"})

        response = self.client.get(reverse("orders:order_list"), {"status": "current", "page": 2})
        self.assertContains(response, "status=current&page=1")
        self.assertContains(response, "+2 more items")
        self.assertEqual(response.context["page_obj"].paginator.count, 12)
        self.assertLessEqual(len(response.context["page_obj"].object_list[0].order_preview_items), 2)
        self.assertContains(response, "Snapshot Tile")
        self.assertContains(response, "60x60 cm")
        self.assertContains(response, "Quantity")
        self.assertNotContains(response, "account-order-card__preview\"")

        small_user = get_user_model().objects.create_user(username="one-order-buyer", password="secret")
        self.order(user=small_user, number=1)

        def history_query_count(user):
            with CaptureQueriesContext(connection) as queries:
                history = get_customer_order_history(user=user, status_filter="current")
                for listed_order in history["page_obj"].object_list:
                    [item.display_product_name for item in listed_order.order_preview_items]
            return len(queries)

        self.assertEqual(history_query_count(small_user), history_query_count(self.user))
        self.assertLessEqual(history_query_count(self.user), 8)

    def test_persian_orders_labels_render_translated(self):
        self.order(status=Order.Status.PENDING, payment_method=Order.PaymentMethod.MOCK, number=30)
        with translation.override("fa"):
            response = self.client.get(reverse("orders:order_list"))
        self.assertContains(response, "سفارش‌های من")
        self.assertContains(response, "وضعیت سفارش")
        self.assertContains(response, "جاری")
        self.assertContains(response, "تحویل‌شده")
        self.assertContains(response, "لغوشده")
        self.assertContains(response, "پرداخت لازم است")
        self.assertContains(response, "مشاهده و پرداخت")
        self.assertContains(response, "اقلام خریداری‌شده")
        self.assertNotContains(response, ">My orders<")
        self.assertNotContains(response, ">Order status<")

    def test_generic_product_thumbnails_are_current_visual_aids_while_snapshots_remain_text_authority(self):
        tile_product = Product.objects.create(
            name="Current Tile Name", slug="current-tile-thumbnail", sku="THUMB-TILE",
            image="products/tile-recognition.jpg",
        )
        laptop_product = Product.objects.create(
            name="Current Laptop Name", slug="current-laptop-thumbnail", sku="THUMB-LAPTOP",
            image="products/laptop-recognition.jpg",
        )
        accessory_product = Product.objects.create(
            name="Current Accessory Name", slug="current-accessory-thumbnail", sku="THUMB-ACCESSORY",
            image="products/accessory-recognition.jpg",
        )
        tile = self.set_snapshot_item(
            self.order(number=101), product_name="Historic Tile", product=tile_product,
            snapshot=self.generic_snapshot(product_type="Tile", name="Historic Tile", configuration=("60×60", "Matte", "Beige")), quantity=40,
        )
        self.set_snapshot_item(
            self.order(number=102), product_name="Historic Laptop", product=laptop_product,
            snapshot=self.generic_snapshot(product_type="Laptop", name="Historic Laptop", configuration=("16 GB RAM", "1 TB SSD", "Gray")),
        )
        self.set_snapshot_item(
            self.order(number=103), product_name="Historic Mouse", product=accessory_product,
            snapshot=self.generic_snapshot(product_type="Accessory", name="Historic Mouse", configuration=("Black", "Bluetooth")), quantity=2,
        )
        self.set_snapshot_item(
            self.order(number=104), product_name="Historic Product Without Media",
            snapshot=self.generic_snapshot(product_type="Accessory", name="Historic Product Without Media"),
        )

        response = self.client.get(reverse("orders:order_list"))
        content = response.content.decode()

        self.assertContains(response, tile.order_history_thumbnail_url)
        self.assertContains(response, "products/laptop-recognition.jpg")
        self.assertContains(response, "products/accessory-recognition.jpg")
        self.assertContains(response, "Historic Tile")
        self.assertContains(response, "Historic Laptop")
        self.assertContains(response, "Historic Mouse")
        self.assertContains(response, "Historic Product Without Media")
        self.assertNotContains(response, "Current Tile Name")
        self.assertNotContains(response, "Current Laptop Name")
        self.assertNotContains(response, "Current Accessory Name")
        self.assertNotIn("· ·", content)
        self.assertEqual(content.count("account-order-card__item-thumbnail"), 4)
        self.assertIn('alt="Historic Tile"', content)
        self.assertIn('account-order-card__item-thumbnail" aria-hidden="true"', content)
        self.assertNotIn("variant__gallery", (Path(__file__).resolve().parents[1] / "orders" / "purchase_presentation.py").read_text())

    def test_twelve_distinct_items_stay_at_two_plus_ten_with_a_bounded_thumbnail_query_budget(self):
        large_order = self.order(number=200)
        large_order.items.all().delete()
        for number in range(1, 13):
            OrderItem.objects.create(
                order=large_order,
                product_name=f"Historic item {number}",
                unit_price=Decimal("1"),
                quantity=number,
                variant_snapshot=self.generic_snapshot(
                    product_type="Accessory", name=f"Historic item {number}",
                    configuration=(f"Specification {number}",),
                ),
            )

        response = self.client.get(reverse("orders:order_list"))
        self.assertContains(response, "Historic item 1")
        self.assertContains(response, "Historic item 2")
        self.assertNotContains(response, "Historic item 3")
        self.assertContains(response, "+10 more items")
        listed_order = response.context["page_obj"].object_list[0]
        self.assertEqual(len(listed_order.order_preview_items), 2)
        self.assertEqual(listed_order.preview_extra_count, 10)

        small_user = get_user_model().objects.create_user(username="thumbnail-one-item", password="secret")
        self.order(user=small_user, number=1)

        def history_query_count(user):
            with CaptureQueriesContext(connection) as queries:
                history = get_customer_order_history(user=user, status_filter="current")
                for listed in history["page_obj"].object_list:
                    for item in listed.order_preview_items:
                        item.order_history_thumbnail_url
                        item.display_product_name
            return len(queries)

        self.assertEqual(history_query_count(small_user), history_query_count(self.user))

# Create your tests here.
