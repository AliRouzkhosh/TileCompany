from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils.translation import override

from orders.cart import _build_cart
from orders.services import reserve_variant_stock
from dashboard.forms import DashboardProductVariantForm

from .currency import Currency
from .models import Product, ProductVariant, VariantPrice
from .services import set_variant_price


class NeutralSellingUnitTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="Generic Device",
            slug="generic-device",
            sku="GENERIC-DEVICE",
            is_active=True,
        )

    def test_unit_is_a_valid_localized_price_unit(self):
        self.assertIn(
            (ProductVariant.PriceUnit.UNIT, "Per Unit"),
            [(value, str(label)) for value, label in ProductVariant.PriceUnit.choices],
        )
        variant = ProductVariant(
            product=self.product,
            sku="DEVICE-BLACK-12-256",
            price_unit=ProductVariant.PriceUnit.UNIT,
            is_active=False,
        )
        variant.full_clean()
        variant.save()

        with override("en"):
            self.assertEqual(variant.get_price_unit_display(), "Per Unit")
        self.assertEqual(
            ProductVariant.objects.get(pk=variant.pk).price_unit,
            ProductVariant.PriceUnit.UNIT,
        )

    def test_legacy_tile_variant_form_excludes_unit(self):
        form = DashboardProductVariantForm()
        choices = list(form.fields["price_unit"].choices)

        self.assertEqual(
            [value for value, _label in choices],
            [
                ProductVariant.PriceUnit.BOX,
                ProductVariant.PriceUnit.M2,
                ProductVariant.PriceUnit.TILE,
            ],
        )
        self.assertNotIn(
            ProductVariant.PriceUnit.UNIT,
            [value for value, _label in choices],
        )

        model_candidate = ProductVariant(
            product=self.product,
            sku="MODEL-UNIT-STILL-VALID",
            price_unit=ProductVariant.PriceUnit.UNIT,
            is_active=False,
        )
        model_candidate.full_clean()

    def test_existing_choices_remain_valid_and_default_remains_box(self):
        expected = {
            ProductVariant.PriceUnit.UNIT,
            ProductVariant.PriceUnit.BOX,
            ProductVariant.PriceUnit.M2,
            ProductVariant.PriceUnit.TILE,
        }
        self.assertEqual(set(ProductVariant.PriceUnit.values), expected)
        self.assertEqual(
            ProductVariant._meta.get_field("price_unit").get_default(),
            ProductVariant.PriceUnit.BOX,
        )
        self.assertFalse(ProductVariant._meta.get_field("price_unit").null)
        self.assertFalse(ProductVariant._meta.get_field("price_unit").blank)

        for index, choice in enumerate(
            (
                ProductVariant.PriceUnit.BOX,
                ProductVariant.PriceUnit.M2,
                ProductVariant.PriceUnit.TILE,
            ),
            start=1,
        ):
            candidate = ProductVariant(
                product=self.product,
                sku=f"LEGACY-UNIT-{index}",
                price_unit=choice,
                is_active=False,
            )
            candidate.full_clean()

        legacy_default = ProductVariant.objects.create(
            product=self.product,
            sku="LEGACY-DEFAULT-BOX",
            is_active=False,
        )
        self.assertEqual(legacy_default.price_unit, ProductVariant.PriceUnit.BOX)

    def test_unit_does_not_change_variant_price_or_inventory_mechanics(self):
        variant = ProductVariant.objects.create(
            product=self.product,
            sku="DEVICE-UNIT-COMMERCE",
            price_unit=ProductVariant.PriceUnit.UNIT,
            stock=5,
            low_stock_threshold=2,
            is_active=True,
        )
        price = set_variant_price(
            variant=variant,
            currency=Currency.USD,
            amount=Decimal("499.00"),
            compare_at_amount=Decimal("549.00"),
        )

        self.assertEqual(price.amount * 2, Decimal("998.00"))
        self.assertEqual(VariantPrice.objects.get(pk=price.pk).variant, variant)

        response = self.client.post(
            reverse("orders:cart_add_variant"),
            {"variant_id": variant.pk, "quantity": 2},
        )
        self.assertEqual(response.status_code, 302)
        cart = _build_cart(self.client)
        self.assertEqual(cart["items"][0]["variant"].pk, variant.pk)
        self.assertEqual(cart["items"][0]["unit_price"], Decimal("499.00"))
        self.assertEqual(cart["items"][0]["subtotal"], Decimal("998.00"))
        self.assertEqual(cart["total"], Decimal("998.00"))

        reserve_variant_stock(variant_id=variant.pk, quantity=2)
        variant.refresh_from_db()
        self.assertEqual(variant.stock, 3)
        self.assertEqual(variant.price_unit, ProductVariant.PriceUnit.UNIT)
