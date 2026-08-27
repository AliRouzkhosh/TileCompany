from decimal import Decimal

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from accounts.models import Address
from orders.cart import _build_cart
from orders.models import Order, OrderItem
from orders.purchase_presentation import build_purchased_line_presentation
from orders.selectors import CheckoutContext, CheckoutItem, CheckoutSummary, get_shipping_methods
from orders.services import create_order
from products.currency import Currency
from products.models import Product, ProductVariant, VariantPrice
from products.pricing import discount_pricing


class DiscountSavingsContinuityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("discount-buyer", password="secret")
        self.address = Address.objects.create(
            user=self.user, first_name="Buyer", last_name="One", phone="123",
            country="IR", province="Tehran", city="Tehran", street_address="1 Street",
        )
        self.discounted = self.variant("Tile", "discount-tile", "DISC-TILE", "DISC-TILE-V", "100.00", "120.00")
        self.regular = self.variant("Laptop", "regular-laptop", "REG-LAP", "REG-LAP-V", "50.00", None)
        self.accessory = self.variant("Accessory", "discount-accessory", "DISC-ACC", "DISC-ACC-V", "20.00", "25.00")

    def variant(self, name, slug, sku, variant_sku, amount, compare_at):
        product = Product.objects.create(name=name, slug=slug, sku=sku)
        variant = ProductVariant.objects.create(product=product, sku=variant_sku, stock=50)
        VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal(amount), compare_at_amount=Decimal(compare_at) if compare_at else None)
        VariantPrice.objects.create(variant=variant, currency=Currency.TOMAN, amount=Decimal(amount) * 100000, compare_at_amount=Decimal(compare_at) * 100000 if compare_at else None)
        return variant

    def cart(self, values, currency=Currency.USD):
        session = self.client.session
        session["cart"] = {f"v_{variant.pk}": quantity for variant, quantity in values}
        session["storefront_currency"] = currency
        session.save()

    def test_authoritative_same_currency_discount_edges(self):
        self.assertEqual(discount_pricing(selling_unit_price="100", reference_unit_price=None).discount_percent, 0)
        self.assertEqual(discount_pricing(selling_unit_price="100", reference_unit_price="100").unit_savings, 0)
        self.assertEqual(discount_pricing(selling_unit_price="100", reference_unit_price="99").line_savings, 0)
        sale = discount_pricing(selling_unit_price="100", reference_unit_price="125", quantity=3)
        self.assertEqual(sale.unit_savings, Decimal("25"))
        self.assertEqual(sale.line_savings, Decimal("75"))
        self.assertEqual(sale.discount_percent, 20)

    def test_cart_and_ajax_reconcile_discounted_and_regular_lines(self):
        self.cart(((self.discounted, 2), (self.regular, 1)))
        cart = _build_cart(self.client.get("/").wsgi_request)
        self.assertEqual(cart["total"], Decimal("250"))
        self.assertEqual(cart["reference_subtotal"], Decimal("290"))
        self.assertEqual(cart["product_savings"], Decimal("40"))
        response = self.client.get(reverse("orders:cart_detail"))
        self.assertContains(response, "Product discounts")
        self.assertContains(response, "17% off")
        self.assertNotContains(response, "You save $40.00")
        self.assertNotContains(response, "data-line-savings")
        self.assertNotContains(response, "data-cart-save-message")
        ajax = self.client.post(reverse("orders:cart_update", args=[f"v_{self.discounted.pk}"]), {"quantity": 3}, HTTP_X_REQUESTED_WITH="XMLHttpRequest").json()
        self.assertEqual(ajax["line_savings"], "60.00")
        self.assertEqual(ajax["product_savings"], "60.00")
        self.assertEqual(ajax["cart_total"], "350.00")

    def test_checkout_summary_reuses_the_same_truth_for_usd_and_toman(self):
        for currency in (Currency.USD, Currency.TOMAN):
            price = VariantPrice.objects.get(variant=self.discounted, currency=currency)
            checkout = CheckoutContext(
                items=[CheckoutItem(key="line", product=self.discounted.product, variant=self.discounted, quantity=2, unit_price=price.amount, subtotal=price.amount * 2, unit_reference_price=price.compare_at_amount)],
                summary=CheckoutSummary(currency=currency, subtotal=price.amount * 2, shipping=Decimal("0"), discount=Decimal("0"), tax=Decimal("0"), total=price.amount * 2, reference_subtotal=price.compare_at_amount * 2, product_savings=(price.compare_at_amount - price.amount) * 2),
                cart_quantity=2, default_address=self.address, addresses=[self.address], shipping_methods=get_shipping_methods(currency), currency=currency, unavailable_variant_ids=[],
            )
            html = render_to_string("orders/components/checkout_summary.html", {"checkout": checkout, "checkout_step": "review"})
            self.assertIn("Product discounts", html)
            self.assertIn("data-sale-price-group", html)
            self.assertNotIn("You save", html)

    def test_new_order_freezes_reference_price_but_legacy_orders_make_no_claim(self):
        checkout = CheckoutContext(
            items=[CheckoutItem(key="line", product=self.discounted.product, variant=self.discounted, quantity=2, unit_price=Decimal("1"), subtotal=Decimal("2"))],
            summary=CheckoutSummary(currency=Currency.USD, subtotal=Decimal("2"), shipping=Decimal("0"), discount=Decimal("0"), tax=Decimal("0"), total=Decimal("2")),
            cart_quantity=2, default_address=self.address, addresses=[self.address], shipping_methods=get_shipping_methods(Currency.USD), currency=Currency.USD, unavailable_variant_ids=[],
        )
        order = create_order(user=self.user, address=self.address, cleaned_data={"shipping_method": "standard", "payment_method": "mock", "delivery_slot": ""}, checkout=checkout)
        item = order.items.get()
        self.assertEqual(item.unit_price, Decimal("100"))
        self.assertEqual(item.unit_reference_price, Decimal("120"))
        VariantPrice.objects.filter(variant=self.discounted, currency=Currency.USD).update(amount=Decimal("90"), compare_at_amount=Decimal("200"))
        history = build_purchased_line_presentation(item)
        self.assertEqual(history.unit_reference_price, Decimal("120"))
        self.assertEqual(history.unit_savings, Decimal("20"))
        legacy = OrderItem.objects.create(order=Order.objects.create(user=self.user, full_name="Legacy", address="1", city="T", phone="1"), product_name="Legacy", unit_price=Decimal("100"), quantity=1)
        self.assertIsNone(build_purchased_line_presentation(legacy).unit_reference_price)
        self.assertEqual(build_purchased_line_presentation(legacy).unit_savings, 0)

    def test_persian_discount_copy_renders_without_english_leakage(self):
        self.cart(((self.discounted, 1),))
        with translation.override("fa"):
            response = self.client.get(reverse("orders:cart_detail"))
        self.assertContains(response, "تخفیف محصولات")
        self.assertContains(response, "اقلام با قیمت اصلی")
        self.assertContains(response, "۱۷٪ تخفیف")
        self.assertNotContains(response, ">Product discounts<")
