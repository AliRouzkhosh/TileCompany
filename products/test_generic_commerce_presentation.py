from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation

from blog.models import Article, ArticleStatus
from dashboard.notifications import get_dashboard_notifications
from orders.models import Order, OrderItem, OrderNote
from orders.services import restore_stock_for_order
from orders.selectors import get_checkout_context
from products.catalog_domain import calculate_variant_combination_signature
from products.commerce_presentation import (
    prepare_commerce_line_presentations,
    quantity_breakdown,
    quantity_phrase,
    selling_unit_presentation,
)
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
    Wishlist,
)


class SellingUnitPresentationTests(TestCase):
    def test_all_model_units_have_approved_quantity_semantics(self):
        expected = {
            ProductVariant.PriceUnit.UNIT: ("Per Unit", "item", "items"),
            ProductVariant.PriceUnit.BOX: ("Per Box", "box", "boxes"),
            ProductVariant.PriceUnit.M2: ("Per m²", "m²", "m²"),
            ProductVariant.PriceUnit.TILE: ("Per Tile", "tile", "tiles"),
            ProductVariant.PriceUnit.BAG: ("Per Bag", "bag", "bags"),
            ProductVariant.PriceUnit.BUCKET: ("Per Bucket", "bucket", "buckets"),
            ProductVariant.PriceUnit.PACK: ("Per Pack", "pack", "packs"),
        }
        self.assertEqual(set(expected), {code for code, _ in ProductVariant.PriceUnit.choices})
        with translation.override("en"):
            for code, values in expected.items():
                unit = selling_unit_presentation(code)
                self.assertEqual((unit.price_label, unit.singular_noun, unit.plural_noun), values)
                self.assertEqual(unit.noun_for(1), values[1])
                self.assertEqual(unit.noun_for(2), values[2])

    def test_quantity_helpers_are_localized_and_keep_mixed_units_separate(self):
        with translation.override("en"):
            self.assertEqual(quantity_phrase("unit", 1), "1 item")
            self.assertEqual(quantity_phrase("unit", 2), "2 items")
            self.assertEqual(quantity_phrase("box", 2), "2 boxes")
            self.assertEqual(quantity_phrase("m2", 2), "2 m²")
            self.assertEqual(quantity_phrase("bag", 2), "2 bags")
            self.assertEqual(quantity_phrase("bucket", 2), "2 buckets")
            self.assertEqual(quantity_phrase("pack", 2), "2 packs")
            self.assertEqual(
                quantity_breakdown((("box", 2), ("unit", 1))),
                "2 boxes, 1 item",
            )
        with translation.override("fa"):
            self.assertNotEqual(selling_unit_presentation("unit").singular_noun, "item")


class GenericCommercePresentationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="commerce-buyer", password="secret-pass-123"
        )
        cls.mobile_type = ProductType.objects.create(code="mobile", name="Mobile")

        cls.brand_definition = AttributeDefinition.objects.create(
            code="brand", name="Brand", data_type="option"
        )
        cls.brand_binding = ProductTypeAttribute.objects.create(
            product_type=cls.mobile_type,
            definition=cls.brand_definition,
            scope="product",
            show_on_storefront=True,
            show_on_product_card=True,
            show_in_specifications=True,
            include_in_purchase_snapshot=True,
            sort_order=1,
        )
        cls.apple = AttributeOption.objects.create(
            definition=cls.brand_definition, code="apple", name="Apple"
        )

        cls.dimension_bindings = {}
        cls.dimension_options = {}
        dimensions = (
            ("color", "Color", "black", "Black"),
            ("ram", "RAM", "12gb", "12 GB"),
            ("storage", "Storage", "256gb", "256 GB"),
        )
        for order, (code, label, option_code, option_label) in enumerate(dimensions, 1):
            definition = AttributeDefinition.objects.create(
                code=code, name=label, data_type="option"
            )
            binding = ProductTypeAttribute.objects.create(
                product_type=cls.mobile_type,
                definition=definition,
                scope="variant",
                is_variant_dimension=True,
                is_required=True,
                show_on_storefront=True,
                show_in_specifications=True,
                include_in_purchase_snapshot=True,
                sort_order=order,
            )
            option = AttributeOption.objects.create(
                definition=definition, code=option_code, name=option_label
            )
            cls.dimension_bindings[code] = binding
            cls.dimension_options[code] = option

        cls.mobile = Product.objects.create(
            product_type=cls.mobile_type,
            name="iPhone 17 Pro",
            slug="iphone-17-pro-commerce",
            sku="IPHONE-17-PRO",
            is_active=True,
        )
        ProductAttributeValue.objects.create(
            product=cls.mobile,
            binding=cls.brand_binding,
            option_value=cls.apple,
        )
        cls.mobile_variant = ProductVariant.objects.create(
            product=cls.mobile,
            sku="IP17-BLK-12-256",
            price_unit=ProductVariant.PriceUnit.UNIT,
            stock=10,
            low_stock_threshold=10,
            is_active=True,
        )
        for code, binding in cls.dimension_bindings.items():
            VariantAttributeValue.objects.create(
                variant=cls.mobile_variant,
                binding=binding,
                option_value=cls.dimension_options[code],
            )
        cls.mobile_variant.combination_signature = calculate_variant_combination_signature(
            cls.mobile_variant
        )
        cls.mobile_variant.save(update_fields=["combination_signature"])
        VariantPrice.objects.create(
            variant=cls.mobile_variant, currency=Currency.USD, amount=Decimal("200")
        )
        VariantPrice.objects.create(
            variant=cls.mobile_variant,
            currency=Currency.TOMAN,
            amount=Decimal("20000000"),
        )

    def _line(self, product=None, variant=None, quantity=2, amount=Decimal("200")):
        product = product or self.mobile
        variant = variant or self.mobile_variant
        return {
            "product": product,
            "variant": variant,
            "quantity": quantity,
            "unit_price": amount,
            "subtotal": amount * quantity,
        }

    def _request_with_cart(self, cart):
        request = RequestFactory().get("/checkout/")
        SessionMiddleware(lambda response: response).process_request(request)
        request.session["cart"] = cart
        request.session["storefront_currency"] = Currency.USD
        request.session.save()
        request.user = self.user
        return request

    def _legacy_tile(self):
        material = Material.objects.create(name="Porcelain", slug="commerce-porcelain")
        color = Color.objects.create(name="Beige", slug="commerce-beige")
        size = Size.objects.create(name="60×60", slug="commerce-60x60")
        finish = Finish.objects.create(name="Glossy", slug="commerce-glossy")
        product = Product.objects.create(
            name="Legacy Tile",
            slug="legacy-tile-commerce",
            sku="LEGACY-TILE-COMMERCE",
            series_name="Stone Series",
            is_active=True,
        )
        product.materials.add(material)
        variant = ProductVariant.objects.create(
            product=product,
            sku="LEGACY-TILE-BOX",
            color=color,
            size=size,
            finish=finish,
            price_unit=ProductVariant.PriceUnit.BOX,
            stock=8,
            low_stock_threshold=8,
            thickness_mm=Decimal("9.50"),
            coverage_per_box_m2=Decimal("1.440"),
            tiles_per_box=4,
            is_active=True,
        )
        VariantPrice.objects.create(
            variant=variant, currency=Currency.USD, amount=Decimal("30")
        )
        return product, variant

    def _generic_tile(self):
        product, variant = self._legacy_tile()
        tile_type = ProductType.objects.create(code="tile", name="Tile")
        product.product_type = tile_type
        product.save(update_fields=["product_type"])

        material_definition = AttributeDefinition.objects.create(
            code="material", name="Material", data_type="option"
        )
        material_binding = ProductTypeAttribute.objects.create(
            product_type=tile_type,
            definition=material_definition,
            scope="product",
            show_on_product_card=True,
            show_in_specifications=True,
            sort_order=1,
        )
        porcelain = AttributeOption.objects.create(
            definition=material_definition, code="porcelain", name="Porcelain"
        )
        ProductAttributeValue.objects.create(
            product=product, binding=material_binding, option_value=porcelain
        )

        dimension_data = (
            (
                self.dimension_bindings["color"].definition,
                "beige",
                "Beige",
            ),
            (
                AttributeDefinition.objects.create(
                    code="size", name="Size", data_type="option"
                ),
                "60x60",
                "60×60",
            ),
            (
                AttributeDefinition.objects.create(
                    code="finish", name="Finish", data_type="option"
                ),
                "glossy",
                "Glossy",
            ),
        )
        for order, (definition, code, label) in enumerate(dimension_data, 1):
            binding = ProductTypeAttribute.objects.create(
                product_type=tile_type,
                definition=definition,
                scope="variant",
                is_variant_dimension=True,
                is_required=True,
                show_in_specifications=True,
                include_in_purchase_snapshot=True,
                sort_order=order,
            )
            option = AttributeOption.objects.create(
                definition=definition, code=code, name=label
            )
            VariantAttributeValue.objects.create(
                variant=variant, binding=binding, option_value=option
            )
        variant.combination_signature = calculate_variant_combination_signature(variant)
        variant.save(update_fields=["combination_signature"])
        return product, variant

    def test_mobile_product_detail_has_generic_brand_and_unit_aware_stock(self):
        response = self.client.get(
            reverse("products:product_detail", args=[self.mobile.slug])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apple")
        self.assertContains(response, "10 items available")
        self.assertNotContains(response, "Brand</dt><dd class=\"font-medium\">—")
        row = response.context["variants_data"][0]
        self.assertEqual(
            row["selling_unit"],
            {
                "code": "unit",
                "price_label": "Per Unit",
                "singular_noun": "item",
                "plural_noun": "items",
            },
        )

    def test_mobile_cart_and_checkout_share_generic_line_presentation(self):
        session = self.client.session
        session["cart"] = {f"v_{self.mobile_variant.pk}": 2}
        session["storefront_currency"] = Currency.USD
        session.save()
        cart = self.client.get(reverse("orders:cart_detail"))
        self.assertContains(cart, "Color: Black")
        self.assertContains(cart, "RAM: 12 GB")
        self.assertContains(cart, "Storage: 256 GB")
        self.assertContains(cart, "2 items")
        self.assertContains(cart, "Total quantity")
        self.assertNotContains(cart, "Total boxes")
        self.assertNotContains(cart, "Coverage:")
        self.assertNotContains(cart, "Tiles per box:")
        self.assertContains(cart, "$400.00")

        checkout = get_checkout_context(
            self._request_with_cart({f"v_{self.mobile_variant.pk}": 2})
        )
        html = render_to_string(
            "orders/components/checkout_summary.html", {"checkout": checkout}
        )
        self.assertIn("Color:", html)
        self.assertIn("Black", html)
        self.assertIn("RAM:", html)
        self.assertIn("Storage:", html)
        self.assertIn("2 items", html)
        self.assertEqual(checkout.summary.subtotal, Decimal("400"))
        self.assertIs(
            checkout.items[0].presentation,
            checkout.items[0].presentation,
        )

    def test_add_message_and_low_stock_notification_use_unit_noun(self):
        response = self.client.post(
            reverse("orders:cart_add_variant"),
            {"variant_id": self.mobile_variant.pk, "quantity": 1},
            follow=True,
        )
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("Added 1 item" in message for message in messages))

        session = self.client.session
        session["cart"] = {}
        session.save()
        limited = self.client.post(
            reverse("orders:cart_add_variant"),
            {"variant_id": self.mobile_variant.pk, "quantity": 20},
            follow=True,
        )
        limited_messages = [
            str(message) for message in get_messages(limited.wsgi_request)
        ]
        self.assertTrue(any("Only 10 items" in message for message in limited_messages))

        notification = next(
            item for item in get_dashboard_notifications()
            if self.mobile_variant.sku in item["message"]
        )
        self.assertIn("10 items remaining", notification["message"])

        _, tile_variant = self._legacy_tile()
        session = self.client.session
        session["cart"] = {}
        session.save()
        tile_response = self.client.post(
            reverse("orders:cart_add_variant"),
            {"variant_id": tile_variant.pk, "quantity": 2},
            follow=True,
        )
        tile_messages = [
            str(message) for message in get_messages(tile_response.wsgi_request)
        ]
        self.assertTrue(any("Added 2 boxes" in message for message in tile_messages))
        tile_notification = next(
            item for item in get_dashboard_notifications()
            if tile_variant.sku in item["message"]
        )
        self.assertIn("8 boxes remaining", tile_notification["message"])

    def test_mixed_cart_preserves_tile_package_facts_and_line_units(self):
        tile, tile_variant = self._generic_tile()
        session = self.client.session
        session["cart"] = {
            f"v_{tile_variant.pk}": 2,
            f"v_{self.mobile_variant.pk}": 1,
        }
        session["storefront_currency"] = Currency.USD
        session.save()
        response = self.client.get(reverse("orders:cart_detail"))
        self.assertContains(response, "2 boxes")
        self.assertContains(response, "1 item")
        self.assertContains(response, "Color: Beige")
        self.assertContains(response, "Size: 60×60")
        self.assertContains(response, "Finish: Glossy")
        self.assertContains(response, "Coverage:")
        self.assertContains(response, "1.440 m² / box")
        self.assertContains(response, "Tiles per box:")
        self.assertNotContains(response, "Total boxes")
        self.assertEqual(response.context["total"], Decimal("260"))
        self.assertEqual(
            {item["variant"].pk for item in response.context["items"]},
            {tile_variant.pk, self.mobile_variant.pk},
        )
        self.assertEqual(
            {item["presentation"].source for item in response.context["items"]},
            {"generic"},
        )

    def test_mixed_restoration_records_each_selling_unit_without_changing_math(self):
        tile, tile_variant = self._legacy_tile()
        order = Order.objects.create(
            user=self.user, full_name="Buyer", address="1", city="Tehran", phone="123"
        )
        OrderItem.objects.create(
            order=order, product=tile, variant=tile_variant,
            product_name=tile.name, unit_price=30, quantity=2,
        )
        OrderItem.objects.create(
            order=order, product=self.mobile, variant=self.mobile_variant,
            product_name=self.mobile.name, unit_price=200, quantity=1,
        )
        result = restore_stock_for_order(order)
        self.assertEqual(result.total_quantity, 3)
        self.assertEqual(dict(result.unit_quantities), {"box": 2, "unit": 1})
        note = OrderNote.objects.get(order=order, operation_code="INVENTORY_RESTORED")
        self.assertIn("2 boxes", note.message)
        self.assertIn("1 item", note.message)

    def test_inactive_products_cannot_be_added_but_remain_removable_in_wishlist(self):
        draft = Product.objects.create(
            product_type=self.mobile_type,
            name="Draft Phone",
            slug="draft-phone-wishlist",
            sku="DRAFT-PHONE-WISHLIST",
            is_active=False,
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("products:wishlist_toggle", args=[draft.pk])
        )
        self.assertEqual(response.status_code, 404)
        Wishlist.objects.create(user=self.user, product=draft)
        page = self.client.get(reverse("products:wishlist"))
        self.assertContains(page, "Draft Phone")
        self.assertContains(page, "Unavailable")
        self.assertNotContains(page, draft.get_absolute_url())
        response = self.client.post(
            reverse("products:wishlist_toggle", args=[draft.pk])
        )
        self.assertRedirects(response, reverse("products:home"))
        self.assertFalse(Wishlist.objects.filter(user=self.user, product=draft).exists())

    def test_blog_related_card_receives_generic_attributes_in_one_batch(self):
        article = Article.objects.create(
            title="Mobile guide",
            slug="mobile-guide-commerce",
            content="Guide",
            status=ArticleStatus.PUBLISHED,
            published_at=timezone.now(),
        )
        article.related_products.add(self.mobile)
        response = self.client.get(reverse("blog:article_detail", args=[article.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apple")
        related = response.context["related_products"]
        self.assertEqual(related[0].catalog_presentation.source, "generic")

    def test_generic_composition_queries_are_fixed_and_templates_do_not_query_eav(self):
        first_line = self._line(quantity=1)
        with CaptureQueriesContext(connection) as single:
            first = prepare_commerce_line_presentations(
                [first_line], currency=Currency.USD, language="en"
            )

        second_product = Product.objects.create(
            product_type=self.mobile_type,
            name="Second Phone",
            slug="second-phone-commerce",
            sku="SECOND-PHONE-COMMERCE",
            is_active=True,
        )
        ProductAttributeValue.objects.create(
            product=second_product, binding=self.brand_binding, option_value=self.apple
        )
        second_variant = ProductVariant.objects.create(
            product=second_product,
            sku="SECOND-BLK-12-256",
            price_unit="unit",
            stock=4,
            is_active=True,
        )
        for code, binding in self.dimension_bindings.items():
            VariantAttributeValue.objects.create(
                variant=second_variant,
                binding=binding,
                option_value=self.dimension_options[code],
            )
        second_variant.combination_signature = calculate_variant_combination_signature(
            second_variant
        )
        second_variant.save(update_fields=["combination_signature"])
        second_line = self._line(
            product=second_product, variant=second_variant, quantity=1
        )
        with CaptureQueriesContext(connection) as multiple:
            prepare_commerce_line_presentations(
                [first_line, second_line], currency=Currency.USD, language="en"
            )
        self.assertEqual(len(single), len(multiple))
        self.assertLessEqual(len(single), 7)

        item = {**first_line, "key": f"v_{self.mobile_variant.pk}"}
        item["presentation"] = first[self.mobile_variant.pk]
        with self.assertNumQueries(0):
            html = render_to_string(
                "orders/components/cart_item.html",
                {"item": item, "currency": Currency.USD},
            )
        self.assertIn("RAM: 12 GB", html)

    def test_generic_public_sources_have_no_legacy_attribute_branches_or_js_unit_map(self):
        root = Path(__file__).resolve().parents[1]
        cart_template = (root / "orders/templates/orders/components/cart_item.html").read_text(encoding="utf-8")
        checkout_template = (root / "orders/templates/orders/components/checkout_summary.html").read_text(encoding="utf-8")
        product_info = (root / "products/templates/products/components/product_info.html").read_text(encoding="utf-8")
        javascript = (root / "static/js/variant_selector.js").read_text(encoding="utf-8")
        for source in (cart_template, checkout_template):
            for forbidden in (
                "variant.size", "variant.color", "variant.finish",
                "product.brand", "product.materials", "product.series_name",
            ):
                self.assertNotIn(forbidden, source)
        self.assertNotIn("product.brand", product_info)
        self.assertNotIn('unit === "box"', javascript)
        self.assertNotIn('unit === "unit"', javascript)
        self.assertIn("variant.selling_unit", javascript)
        for path in (
            root / "orders/services.py",
            root / "dashboard/views/orders.py",
            root / "dashboard/notifications.py",
        ):
            self.assertNotIn("box(es)", path.read_text(encoding="utf-8"))
