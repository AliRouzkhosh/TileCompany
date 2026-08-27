from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from dashboard.forms import DashboardProductForm, DashboardProductVariantForm, catalog_term_form
from dashboard.services import (
    create_product,
    create_product_variant,
    update_product,
    update_product_variant,
)
from products.catalog_backfill import apply_tile_catalog, inspect_tile_catalog
from products.catalog_compat import (
    delete_tile_catalog_term,
    save_tile_catalog_term,
    sync_tile_product_catalog,
    sync_tile_variant_catalog,
)
from products.currency import Currency
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
    Color,
    Design,
    Finish,
    Material,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    Size,
    Usage,
    VariantPrice,
)
from products.selectors import get_product_by_slug, get_product_variant_context


class TileCompatibilityTests(TestCase):
    def setUp(self):
        self.brand = Brand.objects.create(name="Atlas", name_fa="اطلس", slug="atlas")
        self.other_brand = Brand.objects.create(name="Nova", name_fa="نوا", slug="nova")
        self.design = Design.objects.create(name="Stone", name_fa="سنگ", slug="stone")
        self.other_design = Design.objects.create(name="Plain", name_fa="ساده", slug="plain")
        self.material = Material.objects.create(name="Ceramic", name_fa="سرامیک", slug="ceramic")
        self.other_material = Material.objects.create(name="Glass", name_fa="شیشه", slug="glass")
        self.usage = Usage.objects.create(name="Floor", name_fa="کف", slug="floor")
        self.other_usage = Usage.objects.create(name="Wall", name_fa="دیوار", slug="wall")
        self.color = Color.objects.create(name="Black", name_fa="مشکی", slug="black")
        self.other_color = Color.objects.create(name="White", name_fa="سفید", slug="white")
        self.size = Size.objects.create(
            name="60x60", name_fa="۶۰×۶۰", slug="60x60", length_mm=600, width_mm=600
        )
        self.other_size = Size.objects.create(
            name="80x80", name_fa="۸۰×۸۰", slug="80x80", length_mm=800, width_mm=800
        )
        self.finish = Finish.objects.create(name="Matte", name_fa="مات", slug="matte")
        self.other_finish = Finish.objects.create(name="Gloss", name_fa="براق", slug="gloss")
        self.product = Product.objects.create(
            name="Tile", slug="tile", sku="P-TILE", brand=self.brand
        )
        self.product.designs.add(self.design)
        self.product.materials.add(self.material)
        self.product.usages.add(self.usage)
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku="V-TILE",
            color=self.color,
            size=self.size,
            finish=self.finish,
            stock=8,
            low_stock_threshold=2,
        )
        VariantPrice.objects.create(
            variant=self.variant, currency=Currency.USD, amount="25.00"
        )
        apply_tile_catalog()
        self.product.refresh_from_db()
        self.variant.refresh_from_db()

    def product_data(self, **overrides):
        data = {
            "name": "Created Tile",
            "slug": "created-tile",
            "sku": "P-CREATED",
            "brand": str(self.brand.pk),
            "designs": [str(self.design.pk)],
            "materials": [str(self.material.pk)],
            "usages": [str(self.usage.pk)],
            "description": "",
            "is_active": "on",
        }
        data.update(overrides)
        return data

    def variant_data(self, **overrides):
        data = {
            "sku": "V-CREATED",
            "color": str(self.color.pk),
            "size": str(self.size.pk),
            "finish": str(self.finish.pk),
            "price_unit": "box",
            "stock": "9",
            "low_stock_threshold": "3",
            "sort_order": "0",
            "is_active": "on",
            "usd_amount": "30.00",
        }
        data.update(overrides)
        return data

    def generic_codes(self, product, code):
        return set(
            product.attribute_values.filter(binding__definition__code=code)
            .values_list("option_value__code", flat=True)
        )

    def test_product_create_assigns_tile_and_mirrors_all_legacy_values(self):
        form = DashboardProductForm(data=self.product_data())
        self.assertTrue(form.is_valid(), form.errors)
        product = create_product(form)
        self.assertEqual(product.product_type.code, "tile")
        self.assertEqual(self.generic_codes(product, "brand"), {"atlas"})
        self.assertEqual(self.generic_codes(product, "design"), {"stone"})
        self.assertEqual(self.generic_codes(product, "material"), {"ceramic"})
        self.assertEqual(self.generic_codes(product, "usage"), {"floor"})

    def test_product_create_missing_optional_values_creates_no_fake_mirror(self):
        form = DashboardProductForm(
            data=self.product_data(brand="", designs=[], materials=[], usages=[])
        )
        self.assertTrue(form.is_valid(), form.errors)
        product = create_product(form)
        self.assertEqual(product.product_type.code, "tile")
        self.assertFalse(product.attribute_values.exists())

    def test_product_mirror_failure_rolls_back_create(self):
        form = DashboardProductForm(data=self.product_data())
        self.assertTrue(form.is_valid(), form.errors)
        with patch("dashboard.services.sync_tile_product_catalog", side_effect=ValidationError("failed")):
            with self.assertRaises(ValidationError):
                create_product(form)
        self.assertFalse(Product.objects.filter(sku="P-CREATED").exists())

    def test_product_update_replaces_and_clears_exact_memberships(self):
        form = DashboardProductForm(
            data=self.product_data(
                name=self.product.name,
                slug=self.product.slug,
                sku=self.product.sku,
                brand=str(self.other_brand.pk),
                designs=[str(self.other_design.pk)],
                materials=[str(self.other_material.pk)],
                usages=[str(self.other_usage.pk)],
            ),
            instance=self.product,
        )
        self.assertTrue(form.is_valid(), form.errors)
        product = update_product(self.product, form)
        self.assertEqual(self.generic_codes(product, "brand"), {"nova"})
        self.assertEqual(self.generic_codes(product, "design"), {"plain"})
        self.assertEqual(self.generic_codes(product, "material"), {"glass"})
        self.assertEqual(self.generic_codes(product, "usage"), {"wall"})

        clear_form = DashboardProductForm(
            data=self.product_data(
                name=product.name,
                slug=product.slug,
                sku=product.sku,
                brand="",
                designs=[],
                materials=[],
                usages=[],
            ),
            instance=product,
        )
        self.assertTrue(clear_form.is_valid(), clear_form.errors)
        product = update_product(product, clear_form)
        self.assertFalse(product.attribute_values.filter(
            binding__definition__code__in=("brand", "design", "material", "usage")
        ).exists())

    def test_product_sync_does_not_touch_unrelated_generic_definition(self):
        definition = AttributeDefinition.objects.create(
            code="internal-note", name="Internal note", data_type="text"
        )
        binding = ProductTypeAttribute.objects.create(
            product_type=self.product.product_type,
            definition=definition,
            scope="product",
        )
        value = ProductAttributeValue.objects.create(
            product=self.product, binding=binding, text_value="keep"
        )
        sync_tile_product_catalog(self.product)
        value.refresh_from_db()
        self.assertEqual(value.text_value, "keep")

    def test_conflicting_non_null_product_type_is_not_overwritten(self):
        mobile = ProductType.objects.create(code="mobile", name="Mobile")
        product = Product.objects.create(
            name="Conflict", slug="conflict", sku="P-CONFLICT", product_type=mobile
        )
        with self.assertRaises(ValidationError):
            sync_tile_product_catalog(product)
        product.refresh_from_db()
        self.assertEqual(product.product_type, mobile)

    def test_variant_create_mirrors_dimensions_signature_price_stock_and_sku(self):
        form = DashboardProductVariantForm(
            data=self.variant_data(color=str(self.other_color.pk))
        )
        self.assertTrue(form.is_valid(), form.errors)
        variant = create_product_variant(product=self.product, form=form)
        self.assertEqual(variant.sku, "V-CREATED")
        self.assertEqual(variant.stock, 9)
        self.assertEqual(variant.prices.get(currency=Currency.USD).amount, 30)
        self.assertEqual(
            variant.combination_signature, "color=white|finish=matte|size=60x60"
        )
        self.assertEqual(
            {
                row.binding.definition.code: row.option_value.code
                for row in variant.attribute_values.select_related("binding__definition", "option_value")
            },
            {"color": "white", "size": "60x60", "finish": "matte"},
        )

    def test_variant_update_changes_dimension_and_signature(self):
        form = DashboardProductVariantForm(
            data=self.variant_data(
                sku=self.variant.sku,
                color=str(self.other_color.pk),
                size=str(self.other_size.pk),
                finish=str(self.other_finish.pk),
                stock=str(self.variant.stock),
                usd_amount="25.00",
            ),
            instance=self.variant,
        )
        self.assertTrue(form.is_valid(), form.errors)
        variant = update_product_variant(variant=self.variant, form=form)
        self.assertEqual(
            variant.combination_signature, "color=white|finish=gloss|size=80x80"
        )
        self.assertEqual(
            set(variant.attribute_values.values_list("option_value__code", flat=True)),
            {"white", "gloss", "80x80"},
        )

    def test_duplicate_variant_combination_is_rejected_and_rolled_back(self):
        other = ProductVariant.objects.create(
            product=self.product,
            sku="V-OTHER",
            color=self.other_color,
            size=self.other_size,
            finish=self.other_finish,
            is_active=True,
        )
        from products.catalog_compat import sync_tile_variant_catalog
        sync_tile_variant_catalog(other)
        form = DashboardProductVariantForm(
            data=self.variant_data(
                sku=other.sku,
                color=str(self.color.pk),
                size=str(self.size.pk),
                finish=str(self.finish.pk),
            ),
            instance=other,
        )
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(ValidationError):
            update_product_variant(variant=other, form=form)
        other.refresh_from_db()
        self.assertEqual(other.color, self.other_color)
        self.assertEqual(other.combination_signature, "color=white|finish=gloss|size=80x80")

    def test_active_incomplete_variant_rejected_but_inactive_draft_allowed(self):
        active = DashboardProductVariantForm(
            data=self.variant_data(sku="V-INCOMPLETE", finish="")
        )
        self.assertTrue(active.is_valid(), active.errors)
        with self.assertRaises(ValidationError):
            create_product_variant(product=self.product, form=active)
        self.assertFalse(ProductVariant.objects.filter(sku="V-INCOMPLETE").exists())

        inactive = DashboardProductVariantForm(
            data=self.variant_data(sku="V-DRAFT", finish="", is_active="")
        )
        self.assertTrue(inactive.is_valid(), inactive.errors)
        draft = create_product_variant(product=self.product, form=inactive)
        self.assertFalse(draft.is_active)
        self.assertIsNone(draft.combination_signature)
        self.assertEqual(draft.attribute_values.count(), 2)

    def test_completing_inactive_draft_computes_signature(self):
        draft = ProductVariant.objects.create(
            product=self.product,
            sku="V-DRAFT",
            color=self.other_color,
            size=self.other_size,
            is_active=False,
        )
        from products.catalog_compat import sync_tile_variant_catalog
        sync_tile_variant_catalog(draft)
        form = DashboardProductVariantForm(
            data=self.variant_data(
                sku=draft.sku,
                color=str(self.other_color.pk),
                size=str(self.other_size.pk),
                finish=str(self.other_finish.pk),
                is_active="",
            ),
            instance=draft,
        )
        self.assertTrue(form.is_valid(), form.errors)
        draft = update_product_variant(variant=draft, form=form)
        self.assertFalse(draft.is_active)
        self.assertEqual(draft.combination_signature, "color=white|finish=gloss|size=80x80")

    def test_taxonomy_create_copies_localized_labels_and_conflict_rolls_back(self):
        form = catalog_term_form(Color)(
            data={"name": "Blue", "name_fa": "آبی", "slug": "blue"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        blue = save_tile_catalog_term(form)
        option = AttributeOption.objects.get(definition__code="color", code="blue")
        membership = ProductTypeAttributeOption.objects.get(
            binding__product_type__code="tile",
            binding__definition__code="color",
            option=option,
        )
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.sort_order, option.sort_order)

        blue_variant = ProductVariant.objects.create(
            product=self.product,
            sku="V-BLUE",
            color=blue,
            size=self.size,
            finish=self.finish,
        )
        sync_tile_variant_catalog(blue_variant)
        self.assertEqual(
            blue_variant.attribute_values.get(
                binding__definition__code="color"
            ).option_value.code,
            "blue",
        )
        self.assertEqual((option.name, option.name_fa), ("Blue", "آبی"))

        AttributeOption.objects.create(
            definition=AttributeDefinition.objects.get(code="color"),
            code="green",
            name="Conflicting",
        )
        conflict = catalog_term_form(Color)(
            data={"name": "Green", "name_fa": "سبز", "slug": "green"}
        )
        self.assertTrue(conflict.is_valid(), conflict.errors)
        with self.assertRaises(ValidationError):
            save_tile_catalog_term(conflict)
        self.assertFalse(Color.objects.filter(slug="green").exists())
        self.assertTrue(Color.objects.filter(pk=blue.pk).exists())
        self.assertFalse(inspect_tile_catalog(require_complete_generic=True).blocking_issues)

    def test_taxonomy_label_edit_syncs_without_changing_signature(self):
        original_signature = self.variant.combination_signature
        membership_id = ProductTypeAttributeOption.objects.get(
            binding__definition__code="color", option__code="black"
        ).pk
        form = catalog_term_form(Color)(
            data={"name": "Midnight", "name_fa": "نیمه‌شب", "slug": self.color.slug},
            instance=self.color,
        )
        self.assertTrue(form.is_valid(), form.errors)
        save_tile_catalog_term(form)
        option = AttributeOption.objects.get(definition__code="color", code="black")
        self.assertEqual(
            ProductTypeAttributeOption.objects.get(
                binding__definition__code="color", option=option
            ).pk,
            membership_id,
        )
        self.assertEqual((option.name, option.name_fa), ("Midnight", "نیمه‌شب"))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.combination_signature, original_signature)

    def test_mapped_slug_change_and_delete_are_blocked(self):
        form = catalog_term_form(Color)(
            data={"name": self.color.name, "name_fa": self.color.name_fa, "slug": "renamed"},
            instance=self.color,
        )
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(ValidationError):
            save_tile_catalog_term(form)
        self.color.refresh_from_db()
        self.assertEqual(self.color.slug, "black")
        with self.assertRaises(ValidationError):
            delete_tile_catalog_term(self.color)
        self.assertTrue(Color.objects.filter(pk=self.color.pk).exists())

    def test_membership_failure_rolls_back_legacy_term_and_option(self):
        form = catalog_term_form(Color)(
            data={"name": "Azure", "name_fa": "Azure FA", "slug": "azure"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        with patch(
            "products.catalog_compat.ProductTypeAttributeOption.save",
            side_effect=RuntimeError("membership write failed"),
        ):
            with self.assertRaises(RuntimeError):
                save_tile_catalog_term(form)
        self.assertFalse(Color.objects.filter(slug="azure").exists())
        self.assertFalse(
            AttributeOption.objects.filter(definition__code="color", code="azure").exists()
        )

    def test_size_physical_edit_does_not_change_option_code_or_signature(self):
        original_signature = self.variant.combination_signature
        form = catalog_term_form(Size)(
            data={
                "name": self.size.name,
                "name_fa": self.size.name_fa,
                "slug": self.size.slug,
                "length_mm": "610",
                "width_mm": "620",
            },
            instance=self.size,
        )
        self.assertTrue(form.is_valid(), form.errors)
        save_tile_catalog_term(form)
        self.assertTrue(AttributeOption.objects.filter(definition__code="size", code="60x60").exists())
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.combination_signature, original_signature)

    def test_normal_supported_writes_leave_global_reconciliation_clean(self):
        product_form = DashboardProductForm(data=self.product_data())
        self.assertTrue(product_form.is_valid(), product_form.errors)
        created = create_product(product_form)
        variant_form = DashboardProductVariantForm(
            data=self.variant_data(sku="V-RECONCILED", color=str(self.other_color.pk))
        )
        self.assertTrue(variant_form.is_valid(), variant_form.errors)
        create_product_variant(product=created, form=variant_form)
        self.assertFalse(inspect_tile_catalog(require_complete_generic=True).blocking_issues)

    def test_manual_generic_drift_is_detected_not_silently_overwritten(self):
        ProductAttributeValue.objects.filter(
            product=self.product, binding__definition__code="brand"
        ).update(option_value=AttributeOption.objects.get(definition__code="brand", code="nova"))
        report = inspect_tile_catalog()
        self.assertIn("product_value_conflict", {issue.code for issue in report.blocking_issues})
        with self.assertRaises(ValidationError):
            sync_tile_product_catalog(self.product)

    def test_legacy_storefront_variant_contract_and_cart_identity_remain_unchanged(self):
        selected = get_product_by_slug(self.product.slug, Currency.USD)
        self.assertEqual(selected.brand, self.brand)
        request = RequestFactory().get(f"/?variant={self.variant.pk}")
        context = get_product_variant_context(selected, request, Currency.USD)
        row = next(item for item in context["variants_data"] if item["id"] == self.variant.pk)
        self.assertEqual((row["color"], row["size"], row["finish"]), ("black", "60x60", "matte"))

        response = self.client.post(
            reverse("orders:cart_add_variant"),
            {"variant_id": self.variant.pk, "quantity": 1},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["cart"], {f"v_{self.variant.pk}": 1})
