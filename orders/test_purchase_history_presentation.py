from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.db.models import Prefetch
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from accounts.models import Address
from orders.models import Order, OrderItem
from orders.purchase_presentation import (
    build_purchased_line_presentation,
    purchase_history_item_queryset,
)
from orders.selectors import CheckoutContext, CheckoutItem, CheckoutSummary, get_shipping_methods
from orders.services import create_order
from products.currency import Currency
from products.models import (
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
from products.publication_services import publish_product, publish_variant
from dashboard.management.commands.seed_dashboard_roles import Command as SeedDashboardRolesCommand


def v2_snapshot(*, zero_dimensions=False):
    variant_attributes = [] if zero_dimensions else [
        {
            "definition_code": "color", "label_default": "Color", "label_fa": "رنگ",
            "scope": "variant", "data_type": "option", "unit": None,
            "is_dimension": True, "sort_order": 10,
            "values": [{"option_code": "black", "raw_value": "black", "display_default": "Black", "display_fa": "مشکی"}],
        },
        {
            "definition_code": "ram", "label_default": "RAM", "label_fa": "رم",
            "scope": "variant", "data_type": "option", "unit": None,
            "is_dimension": True, "sort_order": 20,
            "values": [{"option_code": "12gb", "raw_value": "12gb", "display_default": "12 GB", "display_fa": "۱۲ گیگابایت"}],
        },
        {
            "definition_code": "storage", "label_default": "Storage", "label_fa": "حافظه",
            "scope": "variant", "data_type": "option", "unit": None,
            "is_dimension": True, "sort_order": 30,
            "values": [{"option_code": "256gb", "raw_value": "256gb", "display_default": "256 GB", "display_fa": "۲۵۶ گیگابایت"}],
        },
        {
            "definition_code": "warranty", "label_default": "Warranty", "label_fa": "گارانتی",
            "scope": "variant", "data_type": "integer", "unit": "months",
            "is_dimension": False, "sort_order": 40,
            "values": [{"option_code": None, "raw_value": 24, "display_default": "24 months", "display_fa": "24 months"}],
        },
        {
            "definition_code": "refurbished", "label_default": "Refurbished", "label_fa": "بازسازی‌شده",
            "scope": "variant", "data_type": "boolean", "unit": None,
            "is_dimension": False, "sort_order": 50,
            "values": [{"option_code": None, "raw_value": False, "display_default": "No", "display_fa": "خیر"}],
        },
        {
            "definition_code": "condition", "label_default": "Condition", "label_fa": "وضعیت",
            "scope": "variant", "data_type": "text", "unit": None,
            "is_dimension": False, "sort_order": 60,
            "values": [{"option_code": None, "raw_value": "Factory sealed", "display_default": "Factory sealed", "display_fa": "Factory sealed"}],
        },
    ]
    return {
        "version": 2,
        "product": {
            "id": 101,
            "product_type": {"code": "mobile", "label_default": "Mobile", "label_fa": "موبایل"},
            "sku": "IPHONE-17-PRO",
            "name_default": "iPhone 17 Pro",
            "name_fa": "آیفون ۱۷ پرو",
        },
        "variant": {
            "id": 202,
            "sku": "IP17-BLK-12-256",
            "combination_signature": "__base__" if zero_dimensions else "color=black|ram=12gb|storage=256gb",
            "selling_unit": {
                "code": "unit", "price_label_default": "Per Unit", "price_label_fa": "به‌ازای هر واحد",
                "singular_noun_default": "item", "singular_noun_fa": "مورد",
                "plural_noun_default": "items", "plural_noun_fa": "مورد",
            },
        },
        "attributes": {
            "product": [
                {
                    "definition_code": "brand", "label_default": "Brand", "label_fa": "برند",
                    "scope": "product", "data_type": "option", "unit": None,
                    "is_dimension": False, "sort_order": 10,
                    "values": [{"option_code": "apple", "raw_value": "apple", "display_default": "Apple", "display_fa": "اپل"}],
                },
                {
                    "definition_code": "screen", "label_default": "Screen", "label_fa": "",
                    "scope": "product", "data_type": "decimal", "unit": "in",
                    "is_dimension": False, "sort_order": 20,
                    "values": [{"option_code": None, "raw_value": "6.300000", "display_default": "6.3 in", "display_fa": "6.3 in"}],
                },
                {
                    "definition_code": "features", "label_default": "Features", "label_fa": "ویژگی‌ها",
                    "scope": "product", "data_type": "option", "unit": None,
                    "is_dimension": False, "sort_order": 30,
                    "values": [
                        {"option_code": "nfc", "raw_value": "nfc", "display_default": "NFC", "display_fa": "ان‌اف‌سی"},
                        {"option_code": "wifi", "raw_value": "wifi", "display_default": "Wi-Fi", "display_fa": "وای‌فای"},
                    ],
                },
            ],
            "variant": variant_attributes,
        },
        "operational": {},
    }


class PurchaseHistoryPresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        SeedDashboardRolesCommand().handle()
        cls.customer = get_user_model().objects.create_user("history-customer", password="secret")
        cls.staff = get_user_model().objects.create_user("history-order-manager", password="secret")
        cls.staff.groups.add(Group.objects.get(name="Order Manager"))
        cls.owner = get_user_model().objects.create_superuser("history-owner", "owner@example.test", "secret")
        cls.order = Order.objects.create(
            user=cls.customer, full_name="History Customer", address="1", city="Tehran",
            phone="123", currency=Currency.USD, total_amount=Decimal("1999.98"),
            refund_status=Order.RefundStatus.REQUESTED,
            payment_provider="ZarinPal", payment_reference="TECH-REF-123",
        )
        cls.v2_item = OrderItem.objects.create(
            order=cls.order, product=None, variant=None,
            product_name="Fallback mutable name", unit_price=Decimal("999.99"), quantity=2,
            variant_snapshot=v2_snapshot(),
        )
        cls.legacy_item = OrderItem.objects.create(
            order=cls.order, product=None, variant=None,
            product_name="Historical Tile (TILE-60)", unit_price=Decimal("20"), quantity=1,
            variant_snapshot={
                "sku": "TILE-60", "size": "60x60cm", "material": "Porcelain",
                "finish": "Gloss", "color": "Beige", "thickness": "9.00 mm",
                "coverage": "1.440 m² / box", "tiles_per_box": "4 tiles / box",
            },
        )
        material = Material.objects.create(
            name="Ceramic", name_fa="سرامیک", slug="ceramic-history-fa"
        )
        color = Color.objects.create(
            name="Brown", name_fa="قهوه‌ای", slug="brown-history-fa"
        )
        finish = Finish.objects.create(
            name="Textured", name_fa="بافت‌دار", slug="textured-history-fa"
        )
        size = Size.objects.create(
            name="60x60 cm", name_fa="۶۰ در ۶۰ سانتی‌متر",
            slug="60x60-history-fa", length_mm=600, width_mm=600,
        )
        cls.localized_product = Product.objects.create(
            name="Urban Concrete", name_fa="بتن شهری",
            slug="urban-concrete-history-fa", sku="URBAN-HISTORY-FA",
        )
        cls.localized_product.materials.add(material)
        cls.localized_variant = ProductVariant.objects.create(
            product=cls.localized_product,
            sku="URBAN-BROWN-60-TEXTURED",
            color=color,
            size=size,
            finish=finish,
            thickness_mm=Decimal("9.00"),
            coverage_per_box_m2=Decimal("1.440"),
            tiles_per_box=4,
            stock=5,
        )
        cls.localized_order = Order.objects.create(
            user=cls.customer,
            full_name="Localized History Customer",
            address="2",
            city="Tehran",
            phone="456",
            currency=Currency.USD,
            total_amount=Decimal("50"),
            refund_status=Order.RefundStatus.REQUESTED,
            payment_provider="ZarinPal",
            payment_reference="TECH-REF-123",
        )
        cls.localized_legacy_item = OrderItem.objects.create(
            order=cls.localized_order,
            product=cls.localized_product,
            variant=cls.localized_variant,
            product_name="Urban Concrete",
            unit_price=Decimal("25"),
            quantity=2,
            variant_snapshot={
                "sku": "URBAN-BROWN-60-TEXTURED",
                "size": "60x60 cm",
                "material": "Ceramic",
                "finish": "Textured",
                "color": "Brown",
                "thickness": "9.00 mm",
                "coverage": "1.440 m² / box",
                "tiles_per_box": "4 tiles / box",
            },
        )

    def test_v2_default_presentation_preserves_identity_types_and_multiple_values(self):
        with translation.override("en"):
            presentation = build_purchased_line_presentation(self.v2_item)
        self.assertEqual(presentation.product_name, "iPhone 17 Pro")
        self.assertEqual(presentation.product_type_code, "mobile")
        self.assertEqual(presentation.product_type_label, "Mobile")
        self.assertEqual(presentation.variant_sku, "IP17-BLK-12-256")
        self.assertEqual(presentation.quantity_noun, "items")
        self.assertEqual(presentation.product_attributes[2].values, ("NFC", "Wi-Fi"))
        by_code = {attribute.definition_code: attribute for attribute in presentation.variant_attributes}
        self.assertEqual(by_code["warranty"].values, ("24 months",))
        self.assertEqual(by_code["refurbished"].values, ("No",))
        self.assertEqual(by_code["condition"].values, ("Factory sealed",))
        self.assertEqual(presentation.product_attributes[1].values, ("6.3 in",))

    def test_v2_persian_uses_frozen_labels_values_and_default_fallback(self):
        item = OrderItem.objects.get(pk=self.v2_item.pk)
        with translation.override("fa"):
            presentation = item.purchase_presentation
        self.assertEqual(presentation.product_name, "آیفون ۱۷ پرو")
        self.assertEqual(presentation.product_type_label, "موبایل")
        self.assertEqual(presentation.product_attributes[0].label, "برند")
        self.assertEqual(presentation.product_attributes[0].values, ("اپل",))
        self.assertEqual(presentation.product_attributes[1].label, "Screen")
        self.assertEqual(presentation.variant_attributes[0].values, ("مشکی",))
        self.assertEqual(presentation.quantity_noun, "مورد")

    def test_legacy_null_fk_preserves_all_snapshot_facts_without_live_lookup(self):
        item = OrderItem.objects.get(pk=self.legacy_item.pk)
        with self.assertNumQueries(0):
            presentation = item.purchase_presentation
        self.assertEqual(presentation.product_name, "Historical Tile (TILE-60)")
        self.assertEqual(presentation.variant_sku, "TILE-60")
        self.assertEqual(
            [attribute.definition_code for attribute in presentation.variant_attributes],
            ["size", "finish", "color"],
        )
        self.assertEqual(presentation.product_attributes[0].values, ("Porcelain",))
        self.assertEqual(
            [fact.code for fact in presentation.operational_facts],
            ["thickness", "coverage", "tiles_per_box"],
        )
        self.assertEqual(presentation.quantity_noun, "")

    def test_persian_legacy_tile_uses_prepared_live_labels_and_localized_units(self):
        item = purchase_history_item_queryset().get(pk=self.localized_legacy_item.pk)
        with translation.override("fa"), self.assertNumQueries(0):
            presentation = item.purchase_presentation

        self.assertEqual(presentation.product_name, "بتن شهری")
        product_values = {
            attribute.definition_code: attribute.values
            for attribute in presentation.product_attributes
        }
        variant_values = {
            attribute.definition_code: attribute.values
            for attribute in presentation.variant_attributes
        }
        self.assertEqual(product_values["material"], ("سرامیک",))
        self.assertEqual(variant_values["color"], ("قهوه‌ای",))
        self.assertEqual(variant_values["finish"], ("بافت‌دار",))
        self.assertEqual(variant_values["size"], ("۶۰ در ۶۰ سانتی‌متر",))
        operational = {
            fact.code: fact.value for fact in presentation.operational_facts
        }
        self.assertEqual(operational["thickness"], "9.00 میلی‌متر")
        self.assertEqual(
            operational["coverage"], "1.440 متر مربع در هر جعبه"
        )
        self.assertEqual(
            operational["tiles_per_box"], "4 عدد کاشی در هر جعبه"
        )
        self.assertEqual(presentation.variant_sku, "URBAN-BROWN-60-TEXTURED")

    def test_persian_customer_list_detail_and_dashboard_share_localized_legacy_dto(self):
        self.client.force_login(self.customer)
        with translation.override("fa"):
            order_list = self.client.get(reverse("orders:order_list"))
            customer_detail = self.client.get(
                reverse("orders:order_detail", args=[self.localized_order.pk])
            )
        self.assertContains(order_list, "بتن شهری")
        for text in (
            "بتن شهری", "سرامیک", "قهوه‌ای", "بافت‌دار",
            "۶۰ در ۶۰ سانتی‌متر", "9.00 میلی‌متر",
            "1.440 متر مربع در هر جعبه", "4 عدد کاشی در هر جعبه",
            "URBAN-BROWN-60-TEXTURED",
        ):
            self.assertContains(customer_detail, text)
        self.assertNotContains(customer_detail, "TECH-REF-123")

        self.client.force_login(self.staff)
        with translation.override("fa"):
            dashboard_detail = self.client.get(
                reverse("dashboard:order_detail", args=[self.localized_order.pk])
            )
        self.client.force_login(self.owner)
        with translation.override("fa"):
            refund_review = self.client.get(
                reverse("dashboard:approve_refund", args=[self.localized_order.pk])
            )
        for response in (dashboard_detail, refund_review):
            for text in (
                "بتن شهری", "سرامیک", "قهوه‌ای", "بافت‌دار",
                "۶۰ در ۶۰ سانتی‌متر", "URBAN-BROWN-60-TEXTURED",
            ):
                self.assertContains(response, text)

    def test_legacy_english_history_remains_frozen_and_unchanged(self):
        self.client.force_login(self.customer)
        with translation.override("en"):
            response = self.client.get(
                reverse("orders:order_detail", args=[self.localized_order.pk])
            )
        for text in (
            "Urban Concrete", "Ceramic", "Brown", "Textured", "60x60 cm",
            "9.00 mm", "1.440 m² / box", "4 tiles / box",
            "URBAN-BROWN-60-TEXTURED",
        ):
            self.assertContains(response, text)
        self.assertNotContains(response, "TECH-REF-123")

    def test_persian_null_fk_legacy_falls_back_safely_without_queries(self):
        item = OrderItem.objects.get(pk=self.legacy_item.pk)
        with translation.override("fa"), self.assertNumQueries(0):
            presentation = item.purchase_presentation
        self.assertEqual(presentation.product_name, "Historical Tile (TILE-60)")
        self.assertEqual(presentation.product_attributes[0].values, ("Porcelain",))
        self.assertEqual(presentation.variant_sku, "TILE-60")

    def test_prepared_shared_history_template_performs_zero_queries(self):
        prepared_order = Order.objects.prefetch_related(
            Prefetch("items", queryset=purchase_history_item_queryset())
        ).get(pk=self.localized_order.pk)
        with translation.override("fa"), self.assertNumQueries(0):
            rendered = render_to_string(
                "orders/components/order_items.html", {"order": prepared_order}
            )
        self.assertIn("بتن شهری", rendered)
        self.assertIn("URBAN-BROWN-60-TEXTURED", rendered)

    def test_zero_dimension_v2_has_no_fake_variant_section(self):
        item = OrderItem.objects.create(
            order=self.order, product_name="Simple", unit_price=Decimal("1"), quantity=1,
            variant_snapshot=v2_snapshot(zero_dimensions=True),
        )
        presentation = item.purchase_presentation
        self.assertEqual(presentation.variant_attributes, ())
        self.assertTrue(presentation.product_attributes)
        self.assertEqual(presentation.variant_sku, "IP17-BLK-12-256")
        self.assertEqual(presentation.quantity_noun, "item")

    def test_cached_presentation_and_multiple_items_add_zero_queries(self):
        items = list(OrderItem.objects.filter(pk__in=[self.v2_item.pk, self.legacy_item.pk]))
        with self.assertNumQueries(0):
            first = [item.purchase_presentation for item in items]
            second = [item.purchase_presentation for item in items]
        self.assertIs(first[0], second[0])

    def test_unknown_version_customer_and_dashboard_degrade_safely_and_log(self):
        item = OrderItem.objects.create(
            order=self.order, product_name="Safe fallback product", unit_price=Decimal("5"), quantity=3,
            variant_snapshot={"version": 999, "variant": {"sku": "SAFE-SKU"}, "secret": "DO-NOT-LEAK"},
        )
        self.client.force_login(self.customer)
        with self.assertLogs("orders.purchase_presentation", level="ERROR") as customer_logs:
            response = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Safe fallback product")
        self.assertContains(response, "SAFE-SKU")
        self.assertContains(response, "Purchase details unavailable")
        self.assertNotContains(response, "DO-NOT-LEAK")
        self.assertIn(f"order_item_id={item.pk}", customer_logs.output[0])

        self.client.force_login(self.staff)
        with self.assertLogs("orders.purchase_presentation", level="ERROR"):
            response = self.client.get(reverse("dashboard:order_detail", args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Purchase snapshot requires review")
        self.assertNotContains(response, "DO-NOT-LEAK")

    def test_unrecognizable_legacy_mapping_degrades_without_live_repair(self):
        item = OrderItem.objects.create(
            order=self.order, product_name="Legacy fallback", unit_price=Decimal("3"), quantity=1,
            variant_snapshot={"mystery": "PRIVATE-RAW-VALUE"},
        )
        with self.assertLogs("orders.purchase_presentation", level="ERROR"):
            with self.assertNumQueries(0):
                presentation = item.purchase_presentation
        self.assertEqual(presentation.status, "malformed")
        self.assertEqual(presentation.product_name, "Legacy fallback")
        self.assertEqual(presentation.specification_chips, ())
        self.assertNotIn("PRIVATE-RAW-VALUE", presentation.customer_diagnostic)

    def test_customer_and_dashboard_render_null_fk_legacy_and_v2_truth(self):
        self.client.force_login(self.customer)
        order_list = self.client.get(reverse("orders:order_list"))
        self.assertContains(order_list, "iPhone 17 Pro")
        self.assertContains(order_list, "Historical Tile (TILE-60)")
        customer = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        for text in ("iPhone 17 Pro", "IP17-BLK-12-256", "Black", "12 GB", "256 GB", "Historical Tile (TILE-60)", "60x60cm", "1.440 m² / box"):
            self.assertContains(customer, text)

        self.client.force_login(self.staff)
        dashboard = self.client.get(reverse("dashboard:order_detail", args=[self.order.pk]))
        for text in ("iPhone 17 Pro", "IP17-BLK-12-256", "Black", "12 GB", "256 GB", "Historical Tile (TILE-60)", "60x60cm", "1.440 m² / box"):
            self.assertContains(dashboard, text)

    def test_refund_review_uses_same_snapshot_presentation(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("dashboard:approve_refund", args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "iPhone 17 Pro")
        self.assertContains(response, "RAM")
        self.assertContains(response, "12 GB")
        self.assertContains(response, "Historical Tile (TILE-60)")

    def test_history_pages_issue_no_generic_value_queries_beyond_bounded_global_navigation(self):
        forbidden_value_tables = (
            "products_attributeoption", "products_productattributevalue",
            "products_variantattributevalue",
        )
        self.client.force_login(self.customer)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("orders:order_detail", args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        sql = "\n".join(query["sql"].lower() for query in captured.captured_queries)
        for table in forbidden_value_tables:
            self.assertNotIn(table, sql)
        navigation_schema_queries = [
            query["sql"].lower()
            for query in captured.captured_queries
            if "products_producttypeattribute" in query["sql"].lower()
        ]
        self.assertEqual(len(navigation_schema_queries), 1)
        self.assertIn("'brand'", navigation_schema_queries[0])
        self.assertIn("'material'", navigation_schema_queries[0])

    def test_history_templates_do_not_interpret_tile_fields_or_gate_on_live_variant(self):
        root = Path(__file__).resolve().parents[1]
        paths = (
            root / "orders/templates/orders/components/order_items.html",
            root / "orders/templates/orders/components/purchased_line_details.html",
            root / "dashboard/templates/dashboard/orders/components/order_item_table.html",
        )
        source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for forbidden in (
            "variant_snapshot", "item.variant.color", "item.variant.size",
            "item.variant.finish", "product.materials", "if item.variant",
        ):
            self.assertNotIn(forbidden, source)


class RealMobileOrderHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        SeedDashboardRolesCommand().handle()
        cls.customer = get_user_model().objects.create_user("mobile-history", password="secret")
        cls.staff = get_user_model().objects.create_user("mobile-order-manager", password="secret")
        cls.staff.groups.add(Group.objects.get(name="Order Manager"))
        cls.address = Address.objects.create(
            user=cls.customer, first_name="Mobile", last_name="Buyer", phone="123",
            country="IR", province="Tehran", city="Tehran", street_address="1 Mobile Way",
        )
        mobile = ProductType.objects.create(code="mobile", name="Mobile", name_fa="موبایل")

        def schema(code, name, scope, order, *, dimension=False, snapshot=True, name_fa=""):
            definition = AttributeDefinition.objects.create(code=code, name=name, name_fa=name_fa, data_type="option" if dimension or code == "brand" else "integer")
            binding = ProductTypeAttribute.objects.create(
                product_type=mobile, definition=definition, scope=scope,
                is_required=True, is_variant_dimension=dimension,
                include_in_purchase_snapshot=snapshot, sort_order=order,
            )
            return definition, binding

        brand_def, brand_binding = schema("brand", "Brand", "product", 10, name_fa="برند")
        cls.color_def, color_binding = schema("color", "Color", "variant", 10, dimension=True, name_fa="رنگ")
        cls.ram_def, ram_binding = schema("ram", "RAM", "variant", 20, dimension=True, name_fa="رم")
        storage_def, storage_binding = schema("storage", "Storage", "variant", 30, dimension=True, name_fa="حافظه")
        warranty_def, warranty_binding = schema("warranty", "Warranty", "variant", 40, name_fa="گارانتی")

        def option(definition, code, name, name_fa):
            return AttributeOption.objects.create(definition=definition, code=code, name=name, name_fa=name_fa)

        apple = option(brand_def, "apple", "Apple", "اپل")
        cls.black = option(cls.color_def, "black", "Black", "مشکی")
        cls.ram12 = option(cls.ram_def, "12gb", "12 GB", "۱۲ گیگابایت")
        storage256 = option(storage_def, "256gb", "256 GB", "۲۵۶ گیگابایت")
        cls.product = Product.objects.create(
            product_type=mobile, name="iPhone 17 Pro", name_fa="آیفون ۱۷ پرو",
            slug="iphone-17-pro-history-real", sku="IPHONE-HISTORY", is_active=False,
        )
        ProductAttributeValue.objects.create(product=cls.product, binding=brand_binding, option_value=apple)
        cls.variant = ProductVariant.objects.create(
            product=cls.product, sku="IP17-HISTORY", price_unit="unit", stock=10,
            is_active=False, combination_signature="color=black|ram=12gb|storage=256gb",
        )
        VariantAttributeValue.objects.create(variant=cls.variant, binding=color_binding, option_value=cls.black)
        VariantAttributeValue.objects.create(variant=cls.variant, binding=ram_binding, option_value=cls.ram12)
        VariantAttributeValue.objects.create(variant=cls.variant, binding=storage_binding, option_value=storage256)
        VariantAttributeValue.objects.create(variant=cls.variant, binding=warranty_binding, integer_value=24)
        VariantPrice.objects.create(variant=cls.variant, currency=Currency.USD, amount=Decimal("999.99"))
        VariantPrice.objects.create(variant=cls.variant, currency=Currency.TOMAN, amount=Decimal("99900000"))
        publish_variant(cls.variant)
        publish_product(cls.product)

    def test_real_v2_order_history_remains_frozen_after_live_renames_and_null_fks(self):
        checkout = CheckoutContext(
            items=[CheckoutItem(
                key=f"v_{self.variant.pk}", product=self.product, variant=self.variant,
                quantity=2, unit_price=Decimal("1"), subtotal=Decimal("2"),
            )],
            summary=CheckoutSummary(
                currency=Currency.USD, subtotal=Decimal("2"), shipping=Decimal("0"),
                discount=Decimal("0"), tax=Decimal("0"), total=Decimal("2"),
            ),
            cart_quantity=2, default_address=self.address, addresses=[self.address],
            shipping_methods=get_shipping_methods(Currency.USD), currency=Currency.USD,
            unavailable_variant_ids=[],
        )
        order = create_order(
            user=self.customer, address=self.address,
            cleaned_data={"shipping_method": "standard", "payment_method": "mock", "delivery_slot": ""},
            checkout=checkout,
        )
        item = order.items.get()
        self.assertEqual(item.variant_snapshot["version"], 2)
        self.product.name = "Renamed live phone"
        self.product.save(update_fields=["name"])
        self.variant.sku = "RENAMED-LIVE-SKU"
        self.variant.save(update_fields=["sku"])
        self.color_def.name = "Shade"
        self.color_def.save(update_fields=["name"])
        self.black.name = "Midnight"
        self.black.save(update_fields=["name"])
        self.ram_def.name = "Memory"
        self.ram_def.save(update_fields=["name"])
        self.ram12.name = "Twelve changed"
        self.ram12.save(update_fields=["name"])
        item.product = None
        item.variant = None
        item.save(update_fields=["product", "variant"])

        self.client.force_login(self.customer)
        response = self.client.get(reverse("orders:order_detail", args=[order.pk]))
        for text in ("iPhone 17 Pro", "IP17-HISTORY", "Apple", "Black", "12 GB", "256 GB", "Warranty", "24", "2 items"):
            self.assertContains(response, text)
        self.assertNotContains(response, "Renamed live phone")
        self.assertNotContains(response, "RENAMED-LIVE-SKU")
        self.assertNotContains(response, "Shade")
        self.assertNotContains(response, "Midnight")
        self.assertNotContains(response, "Memory")
        self.assertNotContains(response, "Twelve changed")
        self.assertContains(response, "$1,999.98")

        self.client.force_login(self.staff)
        dashboard = self.client.get(reverse("dashboard:order_detail", args=[order.pk]))
        self.assertContains(dashboard, "iPhone 17 Pro")
        self.assertContains(dashboard, "IP17-HISTORY")
        self.assertContains(dashboard, "2 items")
