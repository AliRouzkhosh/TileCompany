import copy
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Address
from orders.models import Order, OrderItem
from orders.selectors import CheckoutContext, CheckoutItem, CheckoutSummary, get_shipping_methods
from orders.services import create_order
from products.currency import Currency

from .catalog_domain import serialize_combination_signature
from .models import (
    AttributeDefinition,
    AttributeOption,
    Color,
    Finish,
    Material,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductVariant,
    Size,
    VariantAttributeValue,
    VariantPrice,
)
from .purchase_snapshot import (
    PurchaseSnapshotIntegrityError,
    UnsupportedPurchaseSnapshotVersion,
    build_generic_v2_purchase_snapshot,
    parse_purchase_snapshot,
    prepare_generic_v2_purchase_snapshots,
    prepare_purchase_snapshots,
)
from .publication_services import publish_product, publish_variant


class GenericPurchaseSnapshotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mobile = ProductType.objects.create(
            code="mobile", name="Mobile", name_fa="موبایل"
        )

        def definition(code, name, data_type, *, name_fa="", unit=""):
            return AttributeDefinition.objects.create(
                code=code, name=name, name_fa=name_fa, data_type=data_type, unit=unit
            )

        def binding(definition, scope, order, *, dimension=False, snapshot=False, cardinality="single", required=False):
            return ProductTypeAttribute.objects.create(
                product_type=cls.mobile,
                definition=definition,
                scope=scope,
                cardinality=cardinality,
                is_required=required or dimension,
                is_variant_dimension=dimension,
                include_in_purchase_snapshot=snapshot or dimension,
                sort_order=order,
            )

        cls.brand_def = definition("brand", "Brand", "option", name_fa="برند")
        cls.screen_def = definition("screen_size", "Screen Size", "decimal", name_fa="اندازه صفحه", unit="in")
        cls.tags_def = definition("features", "Features", "option", name_fa="ویژگی‌ها")
        cls.note_def = definition("marketing_note", "Marketing note", "text")
        cls.brand_binding = binding(cls.brand_def, "product", 10, snapshot=True, required=True)
        cls.screen_binding = binding(cls.screen_def, "product", 20, snapshot=True)
        cls.tags_binding = binding(cls.tags_def, "product", 30, snapshot=True, cardinality="multiple")
        cls.note_binding = binding(cls.note_def, "product", 40, snapshot=False)

        cls.color_def = definition("color", "Color", "option", name_fa="رنگ")
        cls.ram_def = definition("ram", "RAM", "option", name_fa="رم")
        cls.storage_def = definition("storage", "Storage", "option", name_fa="حافظه")
        cls.warranty_def = definition("warranty", "Warranty", "integer", name_fa="گارانتی", unit="months")
        cls.refurbished_def = definition("refurbished", "Refurbished", "boolean", name_fa="بازسازی‌شده")
        cls.condition_def = definition("condition_note", "Condition note", "text", name_fa="یادداشت وضعیت")
        cls.internal_def = definition("internal_note", "Internal note", "text")
        cls.color_binding = binding(cls.color_def, "variant", 10, dimension=True)
        cls.ram_binding = binding(cls.ram_def, "variant", 20, dimension=True)
        cls.storage_binding = binding(cls.storage_def, "variant", 30, dimension=True)
        cls.warranty_binding = binding(cls.warranty_def, "variant", 40, snapshot=True)
        cls.refurbished_binding = binding(cls.refurbished_def, "variant", 50, snapshot=True)
        cls.condition_binding = binding(cls.condition_def, "variant", 55, snapshot=True)
        cls.internal_binding = binding(cls.internal_def, "variant", 60, snapshot=False)
        cls.storage_binding.show_in_specifications = False
        cls.storage_binding.save(update_fields=["show_in_specifications"])

        def option(definition, code, name, name_fa="", order=0):
            return AttributeOption.objects.create(
                definition=definition, code=code, name=name, name_fa=name_fa, sort_order=order
            )

        cls.apple = option(cls.brand_def, "apple", "Apple", "اپل")
        cls.wifi = option(cls.tags_def, "wifi", "Wi-Fi", "وای‌فای", 20)
        cls.nfc = option(cls.tags_def, "nfc", "NFC", "ان‌اف‌سی", 10)
        cls.black = option(cls.color_def, "black", "Black", "مشکی")
        cls.ram12 = option(cls.ram_def, "12gb", "12 GB", "۱۲ گیگابایت")
        cls.storage256 = option(cls.storage_def, "256gb", "256 GB", "۲۵۶ گیگابایت")

        cls.product = Product.objects.create(
            product_type=cls.mobile,
            name="iPhone 17 Pro",
            name_fa="آیفون ۱۷ پرو",
            slug="iphone-17-pro-snapshot",
            sku="IPHONE-17-PRO",
            is_active=False,
        )
        ProductAttributeValue.objects.create(product=cls.product, binding=cls.brand_binding, option_value=cls.apple)
        ProductAttributeValue.objects.create(product=cls.product, binding=cls.screen_binding, decimal_value=Decimal("6.300000"))
        ProductAttributeValue.objects.create(product=cls.product, binding=cls.tags_binding, option_value=cls.wifi, sort_order=20)
        ProductAttributeValue.objects.create(product=cls.product, binding=cls.tags_binding, option_value=cls.nfc, sort_order=10)
        ProductAttributeValue.objects.create(product=cls.product, binding=cls.note_binding, text_value="Not historical")

        cls.variant = ProductVariant.objects.create(
            product=cls.product,
            sku="IP17-BLK-12-256",
            price_unit=ProductVariant.PriceUnit.UNIT,
            stock=10,
            is_active=False,
        )
        VariantAttributeValue.objects.create(variant=cls.variant, binding=cls.color_binding, option_value=cls.black)
        VariantAttributeValue.objects.create(variant=cls.variant, binding=cls.ram_binding, option_value=cls.ram12)
        VariantAttributeValue.objects.create(variant=cls.variant, binding=cls.storage_binding, option_value=cls.storage256)
        VariantAttributeValue.objects.create(variant=cls.variant, binding=cls.warranty_binding, integer_value=24)
        VariantAttributeValue.objects.create(variant=cls.variant, binding=cls.refurbished_binding, boolean_value=False)
        VariantAttributeValue.objects.create(variant=cls.variant, binding=cls.condition_binding, text_value="Factory sealed")
        VariantAttributeValue.objects.create(variant=cls.variant, binding=cls.internal_binding, text_value="Do not snapshot")
        cls.variant.combination_signature = serialize_combination_signature(
            (("color", "black"), ("ram", "12gb"), ("storage", "256gb"))
        )
        cls.variant.save(update_fields=["combination_signature"])
        VariantPrice.objects.create(variant=cls.variant, currency=Currency.USD, amount=Decimal("999.99"))
        VariantPrice.objects.create(variant=cls.variant, currency=Currency.TOMAN, amount=Decimal("99900000"))
        publish_variant(cls.variant)
        publish_product(cls.product)

    def _checkout(self, variant=None, quantity=2):
        variant = variant or self.variant
        price = variant.prices.get(currency=Currency.USD).amount
        return CheckoutContext(
            items=[CheckoutItem(
                key=f"v_{variant.pk}", product=variant.product, variant=variant,
                quantity=quantity, unit_price=price, subtotal=price * quantity,
            )],
            summary=CheckoutSummary(
                currency=Currency.USD, subtotal=price * quantity,
                shipping=Decimal("0"), discount=Decimal("0"), tax=Decimal("0"),
                total=price * quantity,
            ),
            cart_quantity=quantity,
            default_address=None,
            addresses=[],
            shipping_methods=get_shipping_methods(Currency.USD),
            currency=Currency.USD,
            unavailable_variant_ids=[],
        )

    def _buyer_and_address(self, suffix=""):
        user = get_user_model().objects.create_user(username=f"snapshot-buyer{suffix}")
        address = Address.objects.create(
            user=user, first_name="Snapshot", last_name="Buyer", phone="123",
            country="IR", province="Tehran", city="Tehran", street_address="1 History Way",
        )
        return user, address

    def test_v2_contract_identity_inclusion_order_and_typed_values(self):
        snapshot = build_generic_v2_purchase_snapshot(self.variant)

        self.assertEqual(set(snapshot), {"version", "product", "variant", "attributes", "operational"})
        self.assertEqual(snapshot["version"], 2)
        self.assertEqual(snapshot["product"]["product_type"]["code"], "mobile")
        self.assertEqual(snapshot["product"]["sku"], "IPHONE-17-PRO")
        self.assertEqual(snapshot["product"]["name_fa"], "آیفون ۱۷ پرو")
        self.assertEqual(snapshot["variant"]["sku"], "IP17-BLK-12-256")
        self.assertEqual(snapshot["variant"]["selling_unit"]["code"], "unit")
        self.assertEqual(snapshot["operational"], {})

        product_entries = snapshot["attributes"]["product"]
        self.assertEqual([entry["definition_code"] for entry in product_entries], ["brand", "screen_size", "features"])
        self.assertEqual(product_entries[1]["values"][0]["raw_value"], "6.300000")
        self.assertEqual(
            [value["option_code"] for value in product_entries[2]["values"]],
            ["nfc", "wifi"],
        )
        self.assertNotIn("marketing_note", json.dumps(snapshot))

        variant_entries = snapshot["attributes"]["variant"]
        self.assertEqual(
            [entry["definition_code"] for entry in variant_entries],
            ["color", "ram", "storage", "warranty", "refurbished", "condition_note"],
        )
        self.assertTrue(all(entry["is_dimension"] for entry in variant_entries[:3]))
        self.assertEqual(variant_entries[3]["values"][0]["raw_value"], 24)
        self.assertIs(variant_entries[4]["values"][0]["raw_value"], False)
        self.assertEqual(variant_entries[5]["values"][0]["raw_value"], "Factory sealed")
        self.assertNotIn("internal_note", json.dumps(snapshot))

    def test_snapshot_freezes_default_and_persian_labels_with_fallback(self):
        snapshot = build_generic_v2_purchase_snapshot(self.variant)
        product_entries = {entry["definition_code"]: entry for entry in snapshot["attributes"]["product"]}
        self.assertEqual(product_entries["brand"]["label_fa"], "برند")
        self.assertEqual(product_entries["brand"]["values"][0]["display_fa"], "اپل")
        self.assertEqual(product_entries["features"]["values"][0]["display_fa"], "ان‌اف‌سی")
        self.assertEqual(snapshot["variant"]["selling_unit"]["price_label_default"], "Per Unit")
        self.assertTrue(snapshot["variant"]["selling_unit"]["price_label_fa"])

    def test_real_create_order_writes_v2_without_changing_commerce_facts(self):
        user, address = self._buyer_and_address()
        order = create_order(
            user=user,
            address=address,
            cleaned_data={"shipping_method": "standard", "payment_method": "mock", "delivery_slot": ""},
            checkout=self._checkout(quantity=2),
        )
        item = order.items.get()
        self.variant.refresh_from_db()
        self.assertEqual(item.variant_snapshot["version"], 2)
        self.assertEqual(item.unit_price, Decimal("999.99"))
        self.assertEqual(item.quantity, 2)
        self.assertEqual(order.currency, Currency.USD)
        self.assertEqual(self.variant.stock, 8)

    def test_snapshot_parser_is_zero_query_and_immune_to_live_mutation(self):
        snapshot = build_generic_v2_purchase_snapshot(self.variant)
        frozen = copy.deepcopy(snapshot)
        self.product.name = "Renamed phone"
        self.product.save(update_fields=["name"])
        self.brand_def.name = "Maker"
        self.brand_def.save(update_fields=["name"])
        self.apple.name = "Renamed Apple"
        self.apple.is_active = False
        self.apple.save(update_fields=["name", "is_active"])
        ProductAttributeValue.objects.filter(product=self.product, binding=self.screen_binding).update(decimal_value=Decimal("9.900000"))

        with self.assertNumQueries(0):
            parsed = parse_purchase_snapshot(snapshot)
        self.assertEqual(snapshot, frozen)
        self.assertEqual(parsed.product["name_default"], "iPhone 17 Pro")
        self.assertEqual(parsed.product_attributes[0].values[0].display_default, "Apple")
        self.assertEqual(parsed.product_attributes[1].values[0].raw_value, "6.300000")

    def test_unknown_and_malformed_v2_fail_explicitly(self):
        with self.assertRaises(UnsupportedPurchaseSnapshotVersion):
            parse_purchase_snapshot({"version": 99})
        with self.assertRaises(PurchaseSnapshotIntegrityError):
            parse_purchase_snapshot({"version": 2, "product": {}})

    def test_legacy_parser_preserves_unversioned_tile_mapping_without_queries(self):
        legacy = {
            "sku": "TILE-1", "size": "60x60", "material": "Porcelain",
            "finish": "Gloss", "color": "Beige", "thickness": "9 mm",
            "coverage": "1.44 m² / box", "tiles_per_box": "4 tiles / box",
        }
        with self.assertNumQueries(0):
            parsed = parse_purchase_snapshot(legacy)
        self.assertIsNone(parsed.version)
        self.assertEqual(parsed.source, "legacy")
        self.assertEqual(parsed.variant["sku"], "TILE-1")
        self.assertEqual(parsed.original_snapshot, legacy)

    def test_snapshot_integrity_failure_rolls_back_real_order_and_stock(self):
        VariantAttributeValue.objects.filter(
            variant=self.variant, binding=self.storage_binding
        ).delete()
        user, address = self._buyer_and_address("-rollback")
        initial_stock = self.variant.stock
        with self.assertRaises(PurchaseSnapshotIntegrityError):
            create_order(
                user=user,
                address=address,
                cleaned_data={"shipping_method": "standard", "payment_method": "mock", "delivery_slot": ""},
                checkout=self._checkout(quantity=1),
            )
        self.assertFalse(Order.objects.filter(user=user).exists())
        self.assertFalse(OrderItem.objects.filter(order__user=user).exists())
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, initial_stock)

    def test_batched_generic_preparation_has_fixed_query_count(self):
        second_product = Product.objects.create(
            product_type=self.mobile, name="iPhone 17 Air",
            slug="iphone-17-air-snapshot", sku="IPHONE-17-AIR", is_active=True,
        )
        ProductAttributeValue.objects.create(
            product=second_product, binding=self.brand_binding, option_value=self.apple
        )
        second = ProductVariant.objects.create(
            product=second_product, sku="IP17-WHT-12-256", price_unit="unit",
            stock=5, is_active=True, combination_signature="color=black|ram=12gb|storage=256gb",
        )
        for binding, option in (
            (self.color_binding, self.black),
            (self.ram_binding, self.ram12),
            (self.storage_binding, self.storage256),
        ):
            VariantAttributeValue.objects.create(variant=second, binding=binding, option_value=option)
        with self.assertNumQueries(4):
            one = prepare_generic_v2_purchase_snapshots([self.variant])
        with self.assertNumQueries(4):
            two = prepare_generic_v2_purchase_snapshots([self.variant, second])
        self.assertEqual(len(one), 1)
        self.assertEqual(len(two), 2)

    def test_representative_mobile_snapshot_size_remains_bounded(self):
        payload = json.dumps(
            build_generic_v2_purchase_snapshot(self.variant),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertLess(len(payload.encode("utf-8")), 10000)


class TilePurchaseSnapshotCompatibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tile = ProductType.objects.create(code="tile", name="Tile", name_fa="کاشی")
        cls.product = Product.objects.create(
            product_type=cls.tile, name="Legacy Tile", name_fa="کاشی قدیمی",
            slug="legacy-tile-snapshot", sku="LEGACY-TILE-SNAPSHOT", is_active=True,
            tiles_per_box=4, coverage_per_box_m2=Decimal("1.440"),
        )
        material = Material.objects.create(name="Porcelain", slug="porcelain-snapshot")
        cls.product.materials.add(material)
        color = Color.objects.create(name="Beige", slug="beige-snapshot")
        size = Size.objects.create(name="60x60cm", slug="60x60-snapshot")
        finish = Finish.objects.create(name="Gloss", slug="gloss-snapshot")
        cls.variant = ProductVariant.objects.create(
            product=cls.product, sku="LEGACY-TILE-60", color=color, size=size,
            finish=finish, length_mm=600, width_mm=600, thickness_mm=Decimal("9.00"),
            tiles_per_box=4, coverage_per_box_m2=Decimal("1.440"), price_unit="box",
            stock=5, is_active=True,
        )
        VariantPrice.objects.create(variant=cls.variant, currency=Currency.USD, amount=Decimal("20"))

        def dimension(code, name, option_code, option_name, order):
            definition = AttributeDefinition.objects.create(code=code, name=name, data_type="option")
            binding = ProductTypeAttribute.objects.create(
                product_type=cls.tile, definition=definition, scope="variant",
                is_variant_dimension=True, is_required=True,
                include_in_purchase_snapshot=True, sort_order=order,
            )
            option = AttributeOption.objects.create(
                definition=definition, code=option_code, name=option_name
            )
            VariantAttributeValue.objects.create(
                variant=cls.variant, binding=binding, option_value=option
            )
            return definition, binding, option

        dimension("color", "Color", "beige", "Beige", 10)
        dimension("size", "Size", "60x60", "60x60cm", 20)
        dimension("finish", "Finish", "gloss", "Gloss", 30)
        cls.variant.combination_signature = "color=beige|finish=gloss|size=60x60"
        cls.variant.save(update_fields=["combination_signature"])

    def test_live_tile_dispatch_retains_exact_legacy_unversioned_shape(self):
        expected = self.variant.order_snapshot
        snapshots = prepare_purchase_snapshots([self.variant])
        self.assertEqual(snapshots[self.variant.pk], expected)
        self.assertNotIn("version", snapshots[self.variant.pk])
        self.assertEqual(
            set(snapshots[self.variant.pk]),
            {"sku", "size", "material", "finish", "color", "thickness", "coverage", "tiles_per_box"},
        )

    def test_real_tile_order_keeps_legacy_snapshot_and_commerce_arithmetic(self):
        user = get_user_model().objects.create_user(username="tile-snapshot-buyer")
        address = Address.objects.create(
            user=user, first_name="Tile", last_name="Buyer", phone="123",
            country="IR", province="Tehran", city="Tehran", street_address="1 Tile Way",
        )
        price = self.variant.prices.get(currency=Currency.USD).amount
        checkout = CheckoutContext(
            items=[CheckoutItem(
                key=f"v_{self.variant.pk}", product=self.product, variant=self.variant,
                quantity=2, unit_price=price, subtotal=price * 2,
            )],
            summary=CheckoutSummary(
                currency=Currency.USD, subtotal=price * 2, shipping=Decimal("0"),
                discount=Decimal("0"), tax=Decimal("0"), total=price * 2,
            ),
            cart_quantity=2, default_address=address, addresses=[address],
            shipping_methods=get_shipping_methods(Currency.USD), currency=Currency.USD,
            unavailable_variant_ids=[],
        )
        expected = self.variant.order_snapshot
        order = create_order(
            user=user, address=address,
            cleaned_data={"shipping_method": "standard", "payment_method": "mock", "delivery_slot": ""},
            checkout=checkout,
        )
        item = order.items.get()
        self.variant.refresh_from_db()
        self.assertEqual(item.variant_snapshot, expected)
        self.assertNotIn("version", item.variant_snapshot)
        self.assertEqual(item.unit_price, Decimal("20"))
        self.assertEqual(item.subtotal, Decimal("40"))
        self.assertEqual(self.variant.stock, 3)

    def test_v2_builder_uses_generic_tile_dimensions_and_keeps_operational_block_separate(self):
        snapshot = build_generic_v2_purchase_snapshot(self.variant)
        dimensions = snapshot["attributes"]["variant"]
        self.assertEqual(
            [(entry["definition_code"], entry["values"][0]["option_code"]) for entry in dimensions],
            [("color", "beige"), ("size", "60x60"), ("finish", "gloss")],
        )
        self.assertEqual(snapshot["variant"]["selling_unit"]["code"], "box")
        self.assertEqual(snapshot["operational"]["tile"]["length_mm"], 600)
        self.assertEqual(snapshot["operational"]["tile"]["coverage_per_box_m2"], "1.440")

    def test_representative_tile_v2_snapshot_size_remains_bounded(self):
        payload = json.dumps(
            build_generic_v2_purchase_snapshot(self.variant),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertLess(len(payload.encode("utf-8")), 8000)
