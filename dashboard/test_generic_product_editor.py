from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from dashboard.forms_generic_product import GenericProductForm
from dashboard.selectors.generic_products import load_generic_product_form_context
from dashboard.services_generic_product import create_generic_product, update_generic_product
from products.catalog_backfill import apply_tile_catalog, inspect_tile_catalog
from products.catalog_framework_audit import run_catalog_framework_audit
from products.catalog_presentation import compose_product_detail_presentation
from products.catalog_read import read_product_attributes
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Brand,
    Category,
    Color,
    Design,
    Finish,
    Material,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    Size,
    Usage,
)


class GenericProductEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Brand.objects.create(name="Atlas", slug="atlas")
        Design.objects.create(name="Plain", slug="plain")
        Material.objects.create(name="Ceramic", slug="ceramic")
        Usage.objects.create(name="Floor", slug="floor")
        Color.objects.create(name="Black", slug="black")
        Size.objects.create(name="60x60", slug="60x60", length_mm=600, width_mm=600)
        Finish.objects.create(name="Matte", slug="matte")
        apply_tile_catalog()

        cls.mobile = ProductType.objects.create(code="mobile", name="Mobile", is_active=True)
        cls.other_type = ProductType.objects.create(code="laptop", name="Laptop", is_active=True)
        cls.inactive_type = ProductType.objects.create(code="retired", name="Retired", is_active=False)
        cls.mobile_category = Category.objects.create(name="Mobile", slug="mobile")
        cls.smartphones = Category.objects.create(
            name="Smartphones", slug="smartphones", parent=cls.mobile_category
        )

        cls.brand = AttributeDefinition.objects.get(code="brand")
        cls.apple = AttributeOption.objects.create(
            definition=cls.brand, code="apple", name="Apple", sort_order=10
        )
        cls.samsung = AttributeOption.objects.create(
            definition=cls.brand, code="samsung", name="Samsung", sort_order=20
        )
        cls.screen = AttributeDefinition.objects.create(
            code="screen_size", name="Screen Size", data_type="decimal", unit="inch",
            validation_rules={"min": "4", "max": "8", "step": "0.1"},
        )
        cls.battery = AttributeDefinition.objects.create(
            code="battery", name="Battery", data_type="integer", unit="mAh",
            validation_rules={"min": 1000, "max": 10000, "step": 100},
        )
        cls.esim = AttributeDefinition.objects.create(
            code="supports_esim", name="Supports eSIM", data_type="boolean"
        )
        cls.tagline = AttributeDefinition.objects.create(
            code="tagline", name="Tagline", data_type="text",
            validation_rules={"min_length": 3, "max_length": 30},
        )
        cls.connectivity = AttributeDefinition.objects.create(
            code="connectivity", name="Connectivity", data_type="option"
        )
        cls.connectivity_options = [
            AttributeOption.objects.create(
                definition=cls.connectivity, code=code, name=name, sort_order=index
            )
            for index, (code, name) in enumerate(
                (("5g", "5G"), ("wifi7", "Wi-Fi 7"), ("nfc", "NFC")), start=1
            )
        ]

        cls.bindings = {}
        for index, (definition, required, cardinality) in enumerate((
            (cls.brand, True, "single"),
            (cls.screen, True, "single"),
            (cls.battery, True, "single"),
            (cls.esim, True, "single"),
            (cls.tagline, False, "single"),
            (cls.connectivity, False, "multiple"),
        ), start=1):
            cls.bindings[definition.code] = ProductTypeAttribute.objects.create(
                product_type=cls.mobile,
                definition=definition,
                scope="product",
                cardinality=cardinality,
                is_required=required,
                show_on_storefront=True,
                show_in_specifications=True,
                sort_order=index,
                is_active=True,
            )
        for index, option in enumerate((cls.apple, cls.samsung), start=1):
            ProductTypeAttributeOption.objects.create(
                binding=cls.bindings["brand"], option=option, sort_order=index
            )

    def core_data(self, **overrides):
        data = {
            "name": "iPhone 17 Pro", "name_fa": "", "slug": "iphone-17-pro",
            "sku": "iphone-17-pro", "description": "", "description_fa": "",
            "category_ids": [str(self.mobile_category.pk), str(self.smartphones.pk)],
            "primary_category_id": str(self.smartphones.pk),
            "is_featured": "", "is_active": "",
            f"attribute_{self.bindings['brand'].pk}": str(self.apple.pk),
            f"attribute_{self.bindings['screen_size'].pk}": "6.3",
            f"attribute_{self.bindings['battery'].pk}": "3500",
            f"attribute_{self.bindings['supports_esim'].pk}": "true",
            f"attribute_{self.bindings['tagline'].pk}": "Pro phone",
            f"attribute_{self.bindings['connectivity'].pk}": [
                str(option.pk) for option in self.connectivity_options
            ],
        }
        data.update(overrides)
        return data

    def form(self, *, product=None, data=None, product_type=None):
        product_type = product_type or (product.product_type if product else self.mobile)
        context = load_generic_product_form_context(product_type, product=product)
        return GenericProductForm(
            data=data, instance=product, product_type=product_type, schema_context=context
        )

    def create_product(self, **overrides):
        form = self.form(data=self.core_data(**overrides))
        self.assertTrue(form.is_valid(), form.errors)
        return create_generic_product(product_type=self.mobile, form=form)

    def value(self, product, code):
        return list(
            product.attribute_values.filter(binding__definition__code=code)
            .select_related("option_value").order_by("sort_order", "id")
        )

    def edit_data(self, product, **overrides):
        data = self.core_data(
            name=product.name, slug=product.slug, sku=product.sku,
        )
        data.update(overrides)
        return data

    def test_mobile_product_round_trip_uses_generic_authority_and_presentation(self):
        product = self.create_product()
        product.refresh_from_db()
        self.assertEqual(product.product_type, self.mobile)
        self.assertFalse(product.is_active)
        self.assertIsNone(product.brand_id)
        self.assertFalse(product.designs.exists())
        self.assertFalse(product.materials.exists())
        self.assertFalse(product.usages.exists())
        self.assertEqual(set(product.categories.all()), {self.mobile_category, self.smartphones})
        self.assertEqual(product.primary_category, self.smartphones)
        self.assertEqual(self.value(product, "brand")[0].option_value, self.apple)
        self.assertEqual(self.value(product, "screen_size")[0].decimal_value, Decimal("6.3"))
        self.assertEqual(self.value(product, "battery")[0].integer_value, 3500)
        self.assertIs(self.value(product, "supports_esim")[0].boolean_value, True)
        bundle = read_product_attributes(product)
        self.assertEqual(bundle.product_type_code, "mobile")
        self.assertEqual(
            {group.definition_code for group in bundle.attributes},
            {"brand", "screen_size", "battery", "supports_esim", "tagline", "connectivity"},
        )
        self.assertEqual(compose_product_detail_presentation(product, selected_variant=None).source, "generic")
        self.assertFalse(run_catalog_framework_audit().blocking_issues)
        self.assertFalse(inspect_tile_catalog(require_complete_generic=True).blocking_issues)

    def test_core_field_boundary_hierarchical_categories_and_inactive_default(self):
        form = self.form()
        for legacy_name in (
            "designs", "materials", "usages", "series_name",
            "series_name_fa", "tiles_per_box", "coverage_per_box_m2",
        ):
            self.assertNotIn(legacy_name, form.fields)
        self.assertNotIn("product_type", form.fields)
        self.assertNotIn("is_active", form.fields)
        self.assertIn("brand", form.fields)

    def test_core_brand_is_written_without_a_schema_brand_attribute(self):
        core_brand = Brand.objects.create(name="Canonical Brand", name_fa="برند اصلی", slug="canonical-brand")
        product = self.create_product(brand=str(core_brand.pk))
        product.refresh_from_db()
        self.assertEqual(product.brand, core_brand)
        self.assertEqual(
            compose_product_detail_presentation(product, selected_variant=None, language="fa").brand_label,
            "برند اصلی",
        )

        form = self.form(product=product, data=self.edit_data(product, brand=""))
        self.assertTrue(form.is_valid(), form.errors)
        update_generic_product(product=product, form=form)
        product.refresh_from_db()
        self.assertIsNone(product.brand_id)
        category_labels = dict(form.fields["category_ids"].choices)
        self.assertTrue(category_labels[str(self.smartphones.pk)].startswith("    "))

    def test_multiple_option_replacement_and_scalar_clear_are_exact(self):
        product = self.create_product()
        data = self.edit_data(
            product,
            **{
                f"attribute_{self.bindings['connectivity'].pk}": [
                    str(self.connectivity_options[0].pk), str(self.connectivity_options[2].pk)
                ],
                f"attribute_{self.bindings['tagline'].pk}": "",
            },
        )
        form = self.form(product=product, data=data)
        self.assertTrue(form.is_valid(), form.errors)
        update_generic_product(product=product, form=form)
        self.assertEqual(
            [row.option_value.code for row in self.value(product, "connectivity")],
            ["5g", "nfc"],
        )
        self.assertFalse(self.value(product, "tagline"))

    def test_boolean_false_is_value_and_optional_missing_is_absent(self):
        product = self.create_product(**{
            f"attribute_{self.bindings['supports_esim'].pk}": "false",
            f"attribute_{self.bindings['tagline'].pk}": "",
            f"attribute_{self.bindings['connectivity'].pk}": [],
        })
        self.assertIs(self.value(product, "supports_esim")[0].boolean_value, False)
        self.assertFalse(self.value(product, "tagline"))
        self.assertFalse(self.value(product, "connectivity"))

    def test_required_numeric_text_and_disallowed_option_validation(self):
        required = self.form(data=self.core_data(**{
            f"attribute_{self.bindings['brand'].pk}": "",
        }))
        self.assertFalse(required.is_valid())
        numeric = self.form(data=self.core_data(**{
            f"attribute_{self.bindings['screen_size'].pk}": "6.35",
            f"attribute_{self.bindings['battery'].pk}": "3550",
        }))
        self.assertFalse(numeric.is_valid())
        text = self.form(data=self.core_data(**{
            f"attribute_{self.bindings['tagline'].pk}": "x",
        }))
        self.assertFalse(text.is_valid())
        atlas = AttributeOption.objects.get(definition=self.brand, code="atlas")
        disallowed = self.form(data=self.core_data(**{
            f"attribute_{self.bindings['brand'].pk}": str(atlas.pk),
        }))
        self.assertFalse(disallowed.is_valid())

    def test_retired_current_option_is_preserved_but_not_newly_selectable(self):
        product = self.create_product()
        self.apple.is_active = False
        self.apple.save(update_fields=["is_active"])
        edit_form = self.form(product=product, data=self.edit_data(product))
        choices = dict(edit_form.fields[f"attribute_{self.bindings['brand'].pk}"].choices)
        self.assertIn(str(self.apple.pk), choices)
        self.assertIn("retired/unavailable", str(choices[str(self.apple.pk)]))
        self.assertTrue(edit_form.is_valid(), edit_form.errors)
        update_generic_product(product=product, form=edit_form)
        self.assertEqual(self.value(product, "brand")[0].option_value, self.apple)

        new_form = self.form(data=self.core_data(slug="other", sku="other", **{
            f"attribute_{self.bindings['brand'].pk}": str(self.apple.pk),
        }))
        self.assertFalse(new_form.is_valid())
        change = self.form(product=product, data=self.edit_data(product, **{
            f"attribute_{self.bindings['brand'].pk}": str(self.samsung.pk),
        }))
        self.assertTrue(change.is_valid(), change.errors)
        update_generic_product(product=product, form=change)
        self.assertEqual(self.value(product, "brand")[0].option_value, self.samsung)

    def test_inactive_historical_binding_value_is_preserved(self):
        product = self.create_product()
        binding = self.bindings["tagline"]
        original_id = self.value(product, "tagline")[0].pk
        binding.is_active = False
        binding.save(update_fields=["is_active"])
        form = self.form(product=product, data=self.edit_data(product))
        self.assertNotIn(f"attribute_{binding.pk}", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        update_generic_product(product=product, form=form)
        self.assertTrue(ProductAttributeValue.objects.filter(pk=original_id).exists())

    def test_product_type_selection_immutability_tile_and_inactive_guards(self):
        product = self.create_product()
        self.assertNotIn("product_type", self.form(product=product).fields)
        product.product_type = self.other_type
        form = self.form(product=Product.objects.get(pk=product.pk), data=self.edit_data(product))
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaisesMessage(ValidationError, "ProductType cannot change"):
            update_generic_product(product=product, form=form)
        with self.assertRaisesMessage(ValidationError, "Inactive ProductTypes"):
            create_generic_product(product_type=self.inactive_type, form=form)
        tile = ProductType.objects.get(code="tile")
        with self.assertRaisesMessage(ValidationError, "Tile Products"):
            create_generic_product(product_type=tile, form=form)
        self.assertEqual(Product.objects.get(pk=product.pk).product_type, self.mobile)

    def test_non_tile_direct_activation_is_ignored_and_draft_remains_editable(self):
        form = self.form(data=self.core_data(is_active="on"))
        self.assertTrue(form.is_valid(), form.errors)
        product = create_generic_product(product_type=self.mobile, form=form)
        self.assertFalse(product.is_active)
        edit = self.form(product=product, data=self.edit_data(product, name="Edited Draft"))
        self.assertTrue(edit.is_valid(), edit.errors)
        update_generic_product(product=product, form=edit)
        product.refresh_from_db()
        self.assertEqual(product.name, "Edited Draft")
        self.assertFalse(product.is_active)

    def test_primary_category_and_uniqueness_validation(self):
        invalid = self.form(data=self.core_data(
            category_ids=[str(self.mobile_category.pk)],
            primary_category_id=str(self.smartphones.pk),
        ))
        self.assertFalse(invalid.is_valid())
        self.create_product()
        duplicate = self.form(data=self.core_data(name="Other"))
        self.assertFalse(duplicate.is_valid())
        self.assertIn("slug", duplicate.errors)
        self.assertIn("sku", duplicate.errors)

    def test_write_failure_rolls_back_core_categories_and_attributes(self):
        form = self.form(data=self.core_data())
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(RuntimeError):
            create_generic_product(
                product_type=self.mobile,
                form=form,
                failure_hook=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
            )
        self.assertFalse(Product.objects.filter(slug="iphone-17-pro").exists())
        self.assertEqual(ProductAttributeValue.objects.filter(product__product_type=self.mobile).count(), 0)

    def test_update_failure_rolls_back_core_category_and_attribute_changes(self):
        product = self.create_product()
        before_brand = self.value(product, "brand")[0].option_value_id
        form = self.form(product=product, data=self.edit_data(product, name="Should Roll Back", **{
            f"attribute_{self.bindings['brand'].pk}": str(self.samsung.pk),
            "category_ids": [str(self.mobile_category.pk)],
            "primary_category_id": str(self.mobile_category.pk),
        }))
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(RuntimeError):
            update_generic_product(
                product=product,
                form=form,
                failure_hook=lambda: (_ for _ in ()).throw(RuntimeError("fail")),
            )
        product.refresh_from_db()
        self.assertEqual(product.name, "iPhone 17 Pro")
        self.assertEqual(product.primary_category, self.smartphones)
        self.assertEqual(self.value(product, "brand")[0].option_value_id, before_brand)

    def test_form_context_query_count_is_bounded_not_per_attribute(self):
        product = self.create_product()
        with self.assertNumQueries(5):
            context = load_generic_product_form_context(self.mobile, product=product)
        with CaptureQueriesContext(connection) as captured:
            GenericProductForm(instance=product, product_type=self.mobile, schema_context=context)
        self.assertLessEqual(len(captured), 1)


class GenericProductEditorViewTests(TestCase):
    def setUp(self):
        call_command("seed_dashboard_roles", verbosity=0)
        self.user = get_user_model().objects.create_user("staff", password="pass")
        self.user.groups.add(Group.objects.get(name="Product Manager"))
        self.client.force_login(self.user)
        self.mobile = ProductType.objects.create(code="mobile", name="Mobile")

    def test_type_selection_create_and_legacy_route_redirects(self):
        self.assertContains(
            self.client.get(reverse("dashboard:products:generic_type_select")), "Mobile"
        )
        data = {
            "name": "Draft", "name_fa": "", "slug": "draft", "sku": "draft",
            "description": "", "description_fa": "", "category_ids": [],
            "primary_category_id": "", "is_featured": "", "is_active": "",
        }
        response = self.client.post(
            reverse("dashboard:products:generic_create", args=[self.mobile.pk]), data
        )
        product = Product.objects.get(slug="draft")
        self.assertRedirects(
            response, reverse("dashboard:products:generic_detail", args=[product.pk])
        )
        self.assertRedirects(
            self.client.get(reverse("dashboard:products:edit", args=[product.pk])),
            reverse("dashboard:products:generic_edit", args=[product.pk]),
        )
        self.assertRedirects(
            self.client.get(reverse("dashboard:products:variants:create", args=[product.pk])),
            reverse("dashboard:products:generic_detail", args=[product.pk]),
        )

    def test_tile_and_inactive_types_cannot_use_generic_create_url(self):
        tile = ProductType.objects.create(code="tile", name="Tile")
        inactive = ProductType.objects.create(code="inactive", name="Inactive", is_active=False)
        self.assertRedirects(
            self.client.get(reverse("dashboard:products:generic_create", args=[tile.pk])),
            reverse("dashboard:products:create"),
        )
        self.assertRedirects(
            self.client.get(reverse("dashboard:products:generic_create", args=[inactive.pk])),
            reverse("dashboard:products:generic_type_select"),
        )
