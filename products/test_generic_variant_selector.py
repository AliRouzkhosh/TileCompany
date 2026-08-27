from decimal import Decimal
from pathlib import Path

from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from products.catalog_domain import calculate_variant_combination_signature
from products.currency import Currency
from products.models import (
    AttributeDefinition, AttributeOption, Product, ProductType,
    ProductTypeAttribute, ProductVariant, VariantAttributeValue, VariantPrice,
)
from products.selectors import get_product_variant_context
from products.variant_selector import (
    available_option_codes, matching_variant_rows, option_visual_state,
    resolve_clicked_variant,
)


class GenericBrowserVariantSelectorTests(TestCase):
    def _schema(self, type_code, dimension_specs):
        product_type = ProductType.objects.create(code=type_code, name=type_code.title())
        product = Product.objects.create(
            product_type=product_type, name=type_code.title(), slug=f"{type_code}-selector",
            sku=f"{type_code.upper()}-SELECTOR", is_active=True,
        )
        bindings, options = {}, {}
        for order, (code, values) in enumerate(dimension_specs):
            definition = AttributeDefinition.objects.create(code=f"{type_code}-{code}", name=code.replace("_", " ").title(), data_type="option")
            # Use requested public dimension code while keeping definitions globally unique across proofs.
            definition.code = code if not AttributeDefinition.objects.filter(code=code).exists() else f"{type_code}_{code}"
            definition.save(update_fields=["code"])
            binding = ProductTypeAttribute.objects.create(
                product_type=product_type, definition=definition, scope="variant",
                is_required=True, is_variant_dimension=True,
                include_in_purchase_snapshot=True, sort_order=order,
            )
            bindings[code] = binding
            options[code] = {
                value: AttributeOption.objects.create(definition=definition, code=value, name=label, sort_order=index)
                for index, (value, label) in enumerate(values)
            }
        return product, bindings, options

    def _variant(self, product, bindings, options, sku, attributes, *, amount, stock=5, active=True):
        variant = ProductVariant.objects.create(
            product=product, sku=sku, price_unit="unit", stock=stock, is_active=active,
        )
        for code, value in attributes.items():
            VariantAttributeValue.objects.create(
                variant=variant, binding=bindings[code], option_value=options[code][value]
            )
        variant.combination_signature = calculate_variant_combination_signature(variant)
        variant.save(update_fields=["combination_signature"])
        VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal(amount))
        return variant

    def test_mobile_groups_params_matching_availability_and_commerce(self):
        product, bindings, options = self._schema("mobile", (
            ("color", (("black", "Black"), ("white", "White"))),
            ("ram", (("8gb", "8 GB"), ("12gb", "12 GB"))),
            ("storage", (("128gb", "128 GB"), ("256gb", "256 GB"))),
        ))
        first = self._variant(product, bindings, options, "M-B-8-128", {"color": "black", "ram": "8gb", "storage": "128gb"}, amount="100", stock=0)
        chosen = self._variant(product, bindings, options, "M-B-12-256", {"color": "black", "ram": "12gb", "storage": "256gb"}, amount="200", stock=7)
        self._variant(product, bindings, options, "M-W-8-256", {"color": "white", "ram": "8gb", "storage": "256gb"}, amount="150")
        request = RequestFactory().get("/product/", {"attr_color": "black", "attr_ram": "12gb"})
        context = get_product_variant_context(product, request, Currency.USD)
        self.assertEqual([group["code"] for group in context["variant_groups"]], ["color", "ram", "storage"])
        self.assertEqual(context["preselected_variant_id"], chosen.pk)
        row = next(row for row in context["variants_data"] if row["id"] == chosen.pk)
        self.assertEqual(row["attributes"], {"color": "black", "ram": "12gb", "storage": "256gb"})
        self.assertEqual((row["amount"], row["stock"], row["price_unit"]), ("200.00", 7, "unit"))
        self.assertEqual({item["id"] for item in matching_variant_rows(context["variants_data"], {"color": "white"})}, {context["variants_data"][2]["id"]})
        self.assertEqual(
            available_option_codes(
                context["variants_data"], {"color": "white"},
                ["color", "ram", "storage"], "ram",
            ),
            {"8gb", "12gb"},
        )
        self.assertIn(first.pk, {row["id"] for row in context["variants_data"]})  # zero stock remains visible

    def test_mobile_click_anchor_resolves_across_conflicting_dimensions(self):
        product, bindings, options = self._schema("mobile-flex", (
            ("tone", (("black", "Black"), ("white", "White"))),
            ("memory", (("8gb", "8 GB"), ("12gb", "12 GB"))),
            ("capacity", (("128gb", "128 GB"), ("256gb", "256 GB"), ("512gb", "512 GB"), ("1tb", "1 TB"))),
        ))
        first = self._variant(
            product, bindings, options, "M-B-8-128",
            {"tone": "black", "memory": "8gb", "capacity": "128gb"}, amount="100", stock=0,
        )
        white = self._variant(
            product, bindings, options, "M-W-12-256",
            {"tone": "white", "memory": "12gb", "capacity": "256gb"}, amount="200",
        )
        black = self._variant(
            product, bindings, options, "M-B-12-512",
            {"tone": "black", "memory": "12gb", "capacity": "512gb"}, amount="300",
        )
        self._variant(
            product, bindings, options, "M-W-8-1T-INACTIVE",
            {"tone": "white", "memory": "8gb", "capacity": "1tb"},
            amount="400", active=False,
        )
        context = get_product_variant_context(product, RequestFactory().get("/"), Currency.USD)
        rows = context["variants_data"]
        selected = dict(next(row for row in rows if row["id"] == first.pk)["attributes"])

        self.assertEqual(
            option_visual_state(rows, selected, "memory", "12gb"),
            "contextually_incompatible",
        )
        self.assertEqual(
            option_visual_state(rows, selected, "memory", "8gb"), "selected"
        )
        capacity_options = {
            option["code"]: option
            for group in context["variant_groups"] if group["code"] == "capacity"
            for option in group["options"]
        }
        self.assertNotIn("1tb", capacity_options)
        self.assertIn("128gb", capacity_options)
        self.assertTrue(capacity_options["128gb"]["available"])
        self.assertEqual(
            option_visual_state(rows, selected, "capacity", "1tb"),
            "globally_unavailable",
        )
        rendered = self.client.get(
            reverse("products:product_detail", args=[product.slug])
        )
        self.assertEqual(rendered.status_code, 200)
        self.assertNotContains(rendered, 'data-value="1tb"')
        self.assertContains(rendered, 'data-value="128gb"')

        resolved = resolve_clicked_variant(rows, selected, "memory", "12gb")
        self.assertEqual(resolved["id"], black.pk)
        selected = dict(resolved["attributes"])
        self.assertEqual(
            option_visual_state(rows, selected, "memory", "12gb"), "selected"
        )
        self.assertEqual(
            option_visual_state(rows, selected, "capacity", "256gb"),
            "contextually_incompatible",
        )
        resolved = resolve_clicked_variant(rows, selected, "capacity", "256gb")
        self.assertEqual(resolved["id"], white.pk)
        selected = dict(resolved["attributes"])
        self.assertEqual(
            option_visual_state(rows, selected, "capacity", "256gb"), "selected"
        )
        resolved = resolve_clicked_variant(rows, selected, "tone", "black")
        self.assertEqual(resolved["id"], black.pk)
        self.assertEqual((resolved["amount"], resolved["stock"], resolved["image"]), ("300.00", 5, ""))
        unavailable = resolve_clicked_variant(rows, resolved["attributes"], "capacity", "128gb")
        self.assertEqual((unavailable["id"], unavailable["stock"]), (first.pk, 0))

        for code in ("tone", "memory", "capacity"):
            represented = {
                row["attributes"][code] for row in rows if code in row["attributes"]
            }
            self.assertEqual(
                available_option_codes(rows, selected, ["tone", "memory", "capacity"], code),
                represented,
            )

    def test_clicked_resolution_ties_use_price_sort_order_then_id(self):
        selection = {"shape": "round", "grade": "standard"}
        rows = [
            {"id": 9, "sort_order": 0, "is_active": True, "is_priced": False,
             "attributes": {"shape": "square", "grade": "standard"}},
            {"id": 8, "sort_order": 2, "is_active": True, "is_priced": True,
             "attributes": {"shape": "square", "grade": "standard"}},
            {"id": 7, "sort_order": 1, "is_active": True, "is_priced": True,
             "attributes": {"shape": "square", "grade": "standard"}},
            {"id": 6, "sort_order": 1, "is_active": True, "is_priced": True,
             "attributes": {"shape": "square", "grade": "standard"}},
        ]
        self.assertEqual(
            resolve_clicked_variant(rows, selection, "shape", "square")["id"], 6
        )

    def test_option_visual_states_separate_context_from_global_availability(self):
        rows = [
            {"id": 1, "is_active": True, "attributes": {"tone": "gray", "scale": "large", "surface": "rough"}},
            {"id": 2, "is_active": True, "attributes": {"tone": "white", "scale": "large", "surface": "rough"}},
            {"id": 3, "is_active": True, "attributes": {"tone": "white", "scale": "small", "surface": "smooth"}},
        ]
        selected = dict(rows[0]["attributes"])
        self.assertEqual(option_visual_state(rows, selected, "tone", "gray"), "selected")
        self.assertEqual(option_visual_state(rows, selected, "tone", "white"), "compatible")
        self.assertEqual(option_visual_state(rows, selected, "scale", "small"), "contextually_incompatible")
        self.assertEqual(option_visual_state(rows, selected, "tone", "black"), "globally_unavailable")
        resolved = resolve_clicked_variant(rows, selected, "scale", "small")
        self.assertEqual(resolved["id"], 3)
        self.assertEqual(option_visual_state(rows, resolved["attributes"], "scale", "small"), "selected")

    def test_tile_dimensions_resolve_in_any_click_order_without_progression(self):
        product, bindings, options = self._schema("tile-flex", (
            ("tone", (("gray", "Gray"), ("white", "White"))),
            ("measure", (("large", "Large"), ("small", "Small"))),
            ("surface", (("textured", "Textured"), ("gloss", "Gloss"), ("natural", "Natural"))),
        ))
        large = self._variant(product, bindings, options, "T-G-L-T", {"tone": "gray", "measure": "large", "surface": "textured"}, amount="10")
        white = self._variant(product, bindings, options, "T-W-S-G", {"tone": "white", "measure": "small", "surface": "gloss"}, amount="11")
        gray = self._variant(product, bindings, options, "T-G-S-N", {"tone": "gray", "measure": "small", "surface": "natural"}, amount="12")
        rows = get_product_variant_context(product, RequestFactory().get("/", {"variant": large.pk}), Currency.USD)["variants_data"]
        selected = dict(next(row for row in rows if row["id"] == large.pk)["attributes"])
        self.assertEqual(option_visual_state(rows, selected, "measure", "small"), "contextually_incompatible")
        self.assertEqual(resolve_clicked_variant(rows, selected, "measure", "small")["id"], gray.pk)
        selected = dict(next(row for row in rows if row["id"] == gray.pk)["attributes"])
        self.assertEqual(resolve_clicked_variant(rows, selected, "surface", "gloss")["id"], white.pk)
        selected = dict(next(row for row in rows if row["id"] == white.pk)["attributes"])
        self.assertEqual(resolve_clicked_variant(rows, selected, "tone", "gray")["id"], gray.pk)

    def test_laptop_uses_color_ram_ssd_without_storage_assumption(self):
        product, bindings, options = self._schema("laptop", (
            ("laptop_color", (("gray", "Gray"),)),
            ("laptop_ram", (("16gb", "16 GB"),)),
            ("ssd", (("1tb", "1 TB"),)),
        ))
        variant = self._variant(product, bindings, options, "L-G-16-1T", {"laptop_color": "gray", "laptop_ram": "16gb", "ssd": "1tb"}, amount="1200")
        context = get_product_variant_context(product, RequestFactory().get("/"), Currency.USD)
        self.assertEqual(context["preselected_variant_id"], variant.pk)
        self.assertEqual(set(context["variants_data"][0]["attributes"]), {"laptop_color", "laptop_ram", "ssd"})

    def test_laptop_arbitrary_dimensions_use_same_clicked_anchor_resolver(self):
        product, bindings, options = self._schema("laptop-flex", (
            ("chassis", (("gray", "Gray"), ("silver", "Silver"))),
            ("memory", (("16", "16"), ("32", "32"))),
            ("drive", (("512", "512"), ("1tb", "1 TB"))),
        ))
        start = self._variant(product, bindings, options, "L-G-16-512", {"chassis": "gray", "memory": "16", "drive": "512"}, amount="1000")
        silver = self._variant(product, bindings, options, "L-S-32-1T", {"chassis": "silver", "memory": "32", "drive": "1tb"}, amount="1200")
        gray = self._variant(product, bindings, options, "L-G-32-512", {"chassis": "gray", "memory": "32", "drive": "512"}, amount="1100")
        rows = get_product_variant_context(product, RequestFactory().get("/", {"variant": start.pk}), Currency.USD)["variants_data"]
        self.assertEqual(option_visual_state(rows, rows[0]["attributes"], "drive", "1tb"), "contextually_incompatible")
        resolved = resolve_clicked_variant(rows, rows[0]["attributes"], "memory", "32")
        self.assertEqual(resolved["id"], gray.pk)
        resolved = resolve_clicked_variant(rows, resolved["attributes"], "drive", "1tb")
        self.assertEqual(resolved["id"], silver.pk)

    def test_zero_dimensions_resolves_base_and_direct_variant_priority(self):
        simple = ProductType.objects.create(code="simple", name="Simple")
        product = Product.objects.create(product_type=simple, name="Simple", slug="simple-selector", sku="SIMPLE-SELECTOR", is_active=True)
        base = ProductVariant.objects.create(product=product, sku="SIMPLE-BASE", combination_signature="__base__", price_unit="unit", stock=3, is_active=True)
        VariantPrice.objects.create(variant=base, currency=Currency.USD, amount=Decimal("25"))
        context = get_product_variant_context(product, RequestFactory().get("/", {"variant": base.pk}), Currency.USD)
        self.assertEqual(context["variant_groups"], [])
        self.assertEqual(context["preselected_variant_id"], base.pk)
        self.assertEqual(context["variants_data"][0]["attributes"], {})
        self.assertEqual(context["variants_data"][0]["stock"], 3)
        invalid = get_product_variant_context(product, RequestFactory().get("/", {"variant": 999999}), Currency.USD)
        self.assertIsNone(invalid["preselected_variant_id"])

    def test_dimension_group_without_active_represented_values_is_not_emitted(self):
        product_type = ProductType.objects.create(code="optional-dimension", name="Optional")
        definition = AttributeDefinition.objects.create(
            code="optional_choice", name="Optional choice", data_type="option"
        )
        ProductTypeAttribute.objects.create(
            product_type=product_type, definition=definition, scope="variant",
            is_required=False, is_variant_dimension=True,
            include_in_purchase_snapshot=True,
        )
        AttributeOption.objects.create(
            definition=definition, code="configured-only", name="Configured only"
        )
        product = Product.objects.create(
            product_type=product_type, name="Optional", slug="optional-dimension-selector",
            sku="OPTIONAL-DIMENSION", is_active=True,
        )
        context = get_product_variant_context(
            product, RequestFactory().get("/"), Currency.USD
        )
        self.assertEqual(context["variant_groups"], [])

    def test_tile_legacy_aliases_normalize_at_one_boundary(self):
        product, bindings, options = self._schema("tile", (
            ("color", (("black", "Black"), ("white", "White"))),
            ("size", (("60x60", "60x60"),)),
            ("finish", (("matte", "Matte"),)),
        ))
        black = self._variant(product, bindings, options, "T-B", {"color": "black", "size": "60x60", "finish": "matte"}, amount="10")
        context = get_product_variant_context(product, RequestFactory().get("/", {"color": "black", "size": "60x60"}), Currency.USD)
        self.assertEqual(context["parameter_source"], "legacy_alias")
        self.assertEqual(context["preselected_variant_id"], black.pk)
        self.assertEqual(context["variants_data"][0]["color"], "black")
        self.assertEqual(context["variants_data"][0]["attributes"]["finish"], "matte")

    def test_complete_tile_fallback_and_bounded_queries(self):
        product = Product.objects.create(name="Legacy", slug="legacy-selector", sku="LEGACY-SELECTOR", is_active=True)
        variant = ProductVariant.objects.create(product=product, sku="LEGACY-V", stock=1, is_active=True)
        VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal("10"))
        with self.assertLogs("products.selectors", level="ERROR"):
            fallback = get_product_variant_context(product, RequestFactory().get("/"), Currency.USD)
        self.assertEqual(fallback["selector_source"], "legacy")
        self.assertIn("attributes", fallback["variants_data"][0])

        mobile, bindings, options = self._schema("bounded", (("tone", (("dark", "Dark"),)),))
        self._variant(mobile, bindings, options, "BOUND", {"tone": "dark"}, amount="10")
        with CaptureQueriesContext(connection) as captured:
            get_product_variant_context(mobile, RequestFactory().get("/"), Currency.USD)
        self.assertLessEqual(len(captured), 12)

    def test_generic_js_and_template_have_no_domain_dimension_branches(self):
        root = Path(__file__).resolve().parents[1]
        javascript = (root / "static" / "js" / "variant_selector.js").read_text(encoding="utf-8").lower()
        template = (root / "products" / "templates" / "products" / "components" / "variant_selector.html").read_text(encoding="utf-8").lower()
        for token in ("color", "size", "finish", "ram", "storage", "tile", "mobile"):
            self.assertNotIn(token, javascript)
            self.assertNotIn(f'data-group="{token}"', template)
        self.assertNotIn("slice(0, index)", javascript)
        self.assertIn("button.disabled = !available", javascript)
        self.assertIn("button.disabled = !priced || variant.stock < 1", javascript)
        self.assertIn('button.dataset.contextincompatible = string(contextincompatible)', javascript)
        self.assertIn('button.setattribute("aria-disabled", string(!available))', javascript)
        self.assertIn('button.classlist.toggle("opacity-60", contextincompatible)', javascript)
