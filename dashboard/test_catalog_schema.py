from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from dashboard.forms_catalog_schema import (
    AttributeDefinitionForm,
    AttributeOptionForm,
    CategorySchemaForm,
    ProductTypeAttributeForm,
    ProductTypeAttributeOptionForm,
    ProductTypeForm,
)
from dashboard.services_catalog_schema import (
    create_attribute_definition,
    create_attribute_option,
    create_binding,
    create_membership,
    create_product_type,
    delete_attribute_definition,
    delete_attribute_option,
    delete_category,
    delete_membership,
    delete_product_type,
    retire_attribute_option,
    retire_product_type,
    save_category,
    set_binding_unrestricted,
    update_attribute_definition,
    update_attribute_option,
    update_binding,
    update_product_type,
)
from products.catalog_backfill import apply_tile_catalog, inspect_tile_catalog
from products.catalog_domain import available_options_for_binding
from products.catalog_framework_audit import run_catalog_framework_audit
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
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    Size,
    Usage,
)


class CatalogSchemaServiceTests(TestCase):
    def product_type_form(self, **overrides):
        data = {"code": "mobile", "name": "Mobile", "name_fa": "", "sort_order": 1, "is_active": "on"}
        data.update(overrides)
        return ProductTypeForm(data=data)

    def definition_form(self, **overrides):
        data = {"code": "ram", "name": "RAM", "name_fa": "", "data_type": "option", "unit": "", "is_active": "on"}
        data.update(overrides)
        return AttributeDefinitionForm(data=data)

    def binding_form(self, product_type, definition, **overrides):
        data = {
            "definition": definition.pk, "scope": "product", "cardinality": "single",
            "is_required": "", "is_variant_dimension": "", "sort_order": 1,
            "is_filterable": "", "facet_mode": "", "is_searchable": "on",
            "show_on_storefront": "on", "show_on_product_card": "",
            "show_in_specifications": "on", "include_in_purchase_snapshot": "",
            "is_active": "on",
        }
        data.update(overrides)
        return ProductTypeAttributeForm(data=data, product_type=product_type)

    def option_form(self, definition, code="8gb", **overrides):
        data = {"code": code, "name": code.upper(), "name_fa": "", "sort_order": 1, "is_active": "on", "presentation_color": ""}
        data.update(overrides)
        return AttributeOptionForm(data=data, definition=definition)

    def membership_form(self, binding, option, **overrides):
        data = {"option": option.pk, "sort_order": 1, "is_active": "on"}
        data.update(overrides)
        return ProductTypeAttributeOptionForm(data=data, binding=binding)

    def test_product_type_create_edit_stable_code_deactivate_and_delete_guard(self):
        form = self.product_type_form()
        self.assertTrue(form.is_valid(), form.errors)
        mobile = create_product_type(form)
        edit = ProductTypeForm(data={"code": "mobile", "name": "Phones", "name_fa": "", "sort_order": 2, "is_active": ""}, instance=mobile)
        self.assertTrue(edit.is_valid(), edit.errors)
        update_product_type(mobile, edit)
        mobile.refresh_from_db()
        self.assertEqual((mobile.name, mobile.sort_order, mobile.is_active), ("Phones", 2, False))
        definition = AttributeDefinition.objects.create(code="brand", name="Brand", data_type="option")
        ProductTypeAttribute.objects.create(product_type=mobile, definition=definition, scope="product")
        code_edit = ProductTypeForm(data={"code": "phone", "name": "Phones", "name_fa": "", "sort_order": 2, "is_active": "on"}, instance=mobile)
        self.assertFalse(code_edit.is_valid())
        with self.assertRaises(ValidationError):
            delete_product_type(mobile)

    def test_definition_supported_types_structured_validation_and_reference_guards(self):
        for code, data_type in (("choice", "option"), ("copy", "text"), ("count", "integer"), ("screen", "decimal"), ("flag", "boolean")):
            form = self.definition_form(code=code, name=code, data_type=data_type)
            self.assertTrue(form.is_valid(), form.errors)
            create_attribute_definition(form)
        numeric = AttributeDefinitionForm(data={
            "code": "weight", "name": "Weight", "name_fa": "", "data_type": "decimal", "unit": "kg",
            "numeric_min": "1.5", "numeric_max": "10", "numeric_step": "0.5", "is_active": "on",
        })
        self.assertTrue(numeric.is_valid(), numeric.errors)
        weight = create_attribute_definition(numeric)
        self.assertEqual(weight.validation_rules, {"min": "1.5", "max": "10", "step": "0.5"})
        invalid = self.definition_form(code="bad", data_type="boolean", unit="kg")
        self.assertFalse(invalid.is_valid())
        option = AttributeOption.objects.create(definition=weight, code="legacy", name="Legacy")
        change_type = AttributeDefinitionForm(data={"code": "weight", "name": "Weight", "name_fa": "", "data_type": "text", "unit": "", "is_active": "on"}, instance=weight)
        self.assertTrue(change_type.is_valid(), change_type.errors)
        with self.assertRaises(ValidationError):
            update_attribute_definition(weight, change_type)
        with self.assertRaises(ValidationError):
            delete_attribute_definition(weight)
        option.delete()

    def test_option_lifecycle_stable_code_and_referenced_delete(self):
        definition = AttributeDefinition.objects.create(code="color", name="Color", data_type="option")
        form = self.option_form(definition, "black", name="Black", name_fa="مشکی", presentation_color="#000000")
        self.assertTrue(form.is_valid(), form.errors)
        black = create_attribute_option(definition, form)
        self.assertEqual(black.presentation_metadata, {"color": "#000000"})
        label_edit = AttributeOptionForm(
            data={"code": "black", "name": "Midnight Black", "name_fa": "", "sort_order": 2, "is_active": "on", "presentation_color": "#000000"},
            instance=black, definition=definition,
        )
        self.assertTrue(label_edit.is_valid(), label_edit.errors)
        update_attribute_option(black, label_edit)
        black.refresh_from_db()
        self.assertEqual((black.name, black.sort_order), ("Midnight Black", 2))
        mobile = ProductType.objects.create(code="mobile", name="Mobile")
        binding = ProductTypeAttribute.objects.create(product_type=mobile, definition=definition, scope="product")
        ProductTypeAttributeOption.objects.create(binding=binding, option=black)
        code_edit = AttributeOptionForm(
            data={"code": "changed", "name": "Black", "name_fa": "", "sort_order": 1, "is_active": "on", "presentation_color": ""},
            instance=black, definition=definition,
        )
        self.assertFalse(code_edit.is_valid())
        black.refresh_from_db()
        retire_attribute_option(black)
        black.refresh_from_db()
        self.assertFalse(black.is_active)
        with self.assertRaises(ValidationError):
            delete_attribute_option(black)

    def test_binding_invariants_and_structural_dimension_guard(self):
        mobile = ProductType.objects.create(code="mobile", name="Mobile")
        color = AttributeDefinition.objects.create(code="color", name="Color", data_type="option")
        invalid = self.binding_form(mobile, color, scope="product", is_variant_dimension="on")
        self.assertFalse(invalid.is_valid())
        dimension_form = self.binding_form(
            mobile, color, scope="variant", is_required="on", is_variant_dimension="on",
            include_in_purchase_snapshot="on",
        )
        self.assertTrue(dimension_form.is_valid(), dimension_form.errors)
        dimension = create_binding(mobile, dimension_form)
        product = Product.objects.create(product_type=mobile, name="Phone", slug="phone", sku="PHONE")
        ProductVariant.objects.create(product=product, sku="PHONE-BLACK", is_active=False)
        edit = ProductTypeAttributeForm(data={
            "definition": color.pk, "scope": "variant", "cardinality": "single", "is_required": "",
            "is_variant_dimension": "", "sort_order": 1, "is_filterable": "", "facet_mode": "",
            "is_searchable": "", "show_on_storefront": "on", "show_on_product_card": "",
            "show_in_specifications": "on", "include_in_purchase_snapshot": "on", "is_active": "on",
        }, instance=dimension, product_type=mobile)
        self.assertTrue(edit.is_valid(), edit.errors)
        with self.assertRaisesMessage(ValidationError, "controlled catalog migration"):
            update_binding(dimension, edit)
        warranty = AttributeDefinition.objects.create(
            code="warranty", name="Warranty", data_type="boolean"
        )
        specification_form = self.binding_form(
            mobile, warranty, scope="variant", sort_order=2
        )
        self.assertTrue(specification_form.is_valid(), specification_form.errors)
        specification = create_binding(mobile, specification_form)
        self.assertFalse(specification.is_variant_dimension)

    def test_required_product_binding_reports_existing_product_impact(self):
        mobile = ProductType.objects.create(code="mobile", name="Mobile")
        Product.objects.create(product_type=mobile, name="Phone", slug="phone", sku="PHONE")
        brand = AttributeDefinition.objects.create(code="brand", name="Brand", data_type="option")
        form = self.binding_form(mobile, brand, is_required="on")
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaisesMessage(ValidationError, "1 products lack values"):
            create_binding(mobile, form)

    def test_membership_restricted_unrestricted_order_and_last_row_guard(self):
        mobile = ProductType.objects.create(code="mobile", name="Mobile")
        brand = AttributeDefinition.objects.create(code="brand", name="Brand", data_type="option")
        apple = AttributeOption.objects.create(definition=brand, code="apple", name="Apple", sort_order=10)
        samsung = AttributeOption.objects.create(definition=brand, code="samsung", name="Samsung", sort_order=20)
        binding = ProductTypeAttribute.objects.create(product_type=mobile, definition=brand, scope="product")
        self.assertEqual(set(available_options_for_binding(binding)), {apple, samsung})
        form = self.membership_form(binding, samsung, sort_order=2)
        self.assertTrue(form.is_valid(), form.errors)
        membership = create_membership(binding, form)
        self.assertEqual(list(available_options_for_binding(binding)), [samsung])
        with self.assertRaisesMessage(ValidationError, "unrestricted mode"):
            delete_membership(membership)
        set_binding_unrestricted(binding, confirmed=True)
        self.assertEqual(set(available_options_for_binding(binding)), {apple, samsung})

    def test_category_root_child_move_cycle_flags_and_delete_protection(self):
        root_form = CategorySchemaForm(data={"name": "Electronics", "name_fa": "", "slug": "electronics", "parent": "", "sort_order": 1, "is_active": "on", "show_in_navigation": "on"})
        self.assertTrue(root_form.is_valid(), root_form.errors)
        root = save_category(root_form)
        child_form = CategorySchemaForm(data={"name": "Mobile", "name_fa": "", "slug": "mobile", "parent": root.pk, "sort_order": 2, "is_active": "on", "show_in_navigation": ""})
        self.assertTrue(child_form.is_valid(), child_form.errors)
        child = save_category(child_form)
        cycle = CategorySchemaForm(data={"name": "Electronics", "name_fa": "", "slug": "electronics", "parent": child.pk, "sort_order": 1, "is_active": "on", "show_in_navigation": "on"}, instance=root)
        self.assertFalse(cycle.is_valid())
        with self.assertRaises(ValidationError):
            delete_category(root)
        move = CategorySchemaForm(data={"name": "Mobile", "name_fa": "", "slug": "mobile", "parent": "", "sort_order": 3, "is_active": "", "show_in_navigation": ""}, instance=child)
        self.assertTrue(move.is_valid(), move.errors)
        save_category(move)
        child.refresh_from_db()
        self.assertIsNone(child.parent_id)
        self.assertFalse(child.is_active)


class TileSchemaProtectionTests(TestCase):
    def setUp(self):
        Brand.objects.create(name="Atlas", slug="atlas")
        Design.objects.create(name="Plain", slug="plain")
        Material.objects.create(name="Ceramic", slug="ceramic")
        Usage.objects.create(name="Floor", slug="floor")
        Color.objects.create(name="Black", slug="black")
        Size.objects.create(name="60x60", slug="60x60", length_mm=600, width_mm=600)
        Finish.objects.create(name="Matte", slug="matte")
        apply_tile_catalog()
        self.tile = ProductType.objects.get(code="tile")

    def test_tile_binding_membership_and_mapped_option_mutations_are_blocked(self):
        color = self.tile.attribute_bindings.get(definition__code="color")
        with self.assertRaises(ValidationError):
            from dashboard.services_catalog_schema import retire_binding
            retire_binding(color)
        apple = AttributeOption.objects.create(
            definition=AttributeDefinition.objects.get(code="brand"), code="apple", name="Apple"
        )
        brand_binding = self.tile.attribute_bindings.get(definition__code="brand")
        form = ProductTypeAttributeOptionForm(data={"option": apple.pk, "sort_order": 99, "is_active": "on"}, binding=brand_binding)
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(ValidationError):
            create_membership(brand_binding, form)
        mapped = AttributeOption.objects.get(definition__code="color", code="black")
        with self.assertRaises(ValidationError):
            delete_attribute_option(mapped)
        code_form = AttributeOptionForm(
            data={"code": "renamed", "name": mapped.name, "name_fa": "", "sort_order": mapped.sort_order, "is_active": "on", "presentation_color": ""},
            instance=mapped, definition=mapped.definition,
        )
        self.assertFalse(code_form.is_valid())
        size_binding = self.tile.attribute_bindings.get(definition__code="size")
        scope_form = ProductTypeAttributeForm(data={
            "definition": size_binding.definition_id, "scope": "product", "cardinality": "single",
            "is_required": "", "is_variant_dimension": "", "sort_order": size_binding.sort_order,
            "is_filterable": "on", "facet_mode": "terms", "is_searchable": "on",
            "show_on_storefront": "on", "show_on_product_card": "", "show_in_specifications": "on",
            "include_in_purchase_snapshot": "on", "is_active": "on",
        }, instance=size_binding, product_type=self.tile)
        self.assertTrue(scope_form.is_valid(), scope_form.errors)
        with self.assertRaises(ValidationError):
            update_binding(size_binding, scope_form)
        self.assertFalse(inspect_tile_catalog(require_complete_generic=True).blocking_issues)

    def test_tile_product_type_and_shared_definition_are_read_only(self):
        form = ProductTypeForm(data={"code": "tile", "name": "Changed", "name_fa": "", "sort_order": 0, "is_active": "on"}, instance=self.tile)
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(ValidationError):
            update_product_type(self.tile, form)
        definition = AttributeDefinition.objects.get(code="size")
        definition_form = AttributeDefinitionForm(data={"code": "size", "name": "Changed", "name_fa": "", "data_type": "option", "unit": "", "is_active": "on"}, instance=definition)
        self.assertTrue(definition_form.is_valid(), definition_form.errors)
        with self.assertRaises(ValidationError):
            update_attribute_definition(definition, definition_form)

    def test_synthetic_mobile_schema_uses_dashboard_services_and_shared_definitions(self):
        type_form = ProductTypeForm(data={"code": "mobile", "name": "Mobile", "name_fa": "", "sort_order": 10, "is_active": "on"})
        self.assertTrue(type_form.is_valid(), type_form.errors)
        mobile = create_product_type(type_form)

        definitions = {
            "brand": AttributeDefinition.objects.get(code="brand"),
            "color": AttributeDefinition.objects.get(code="color"),
        }
        for code, name, data_type, unit in (
            ("screen_size", "Screen Size", "decimal", "inch"),
            ("ram", "RAM", "option", ""),
            ("storage", "Storage", "option", ""),
        ):
            form = AttributeDefinitionForm(data={
                "code": code, "name": name, "name_fa": "", "data_type": data_type,
                "unit": unit, "is_active": "on",
            })
            self.assertTrue(form.is_valid(), form.errors)
            definitions[code] = create_attribute_definition(form)

        options = {}
        for code, pairs in {
            "brand": (("apple", "Apple"), ("samsung", "Samsung")),
            "color": (("white", "White"),),
            "ram": (("8gb", "8 GB"), ("12gb", "12 GB")),
            "storage": (("128gb", "128 GB"), ("256gb", "256 GB")),
        }.items():
            options[code] = []
            for index, (option_code, name) in enumerate(pairs, start=1):
                form = AttributeOptionForm(data={
                    "code": option_code, "name": name, "name_fa": "", "sort_order": index,
                    "is_active": "on", "presentation_color": "",
                }, definition=definitions[code])
                self.assertTrue(form.is_valid(), form.errors)
                options[code].append(create_attribute_option(definitions[code], form))
        options["color"].insert(0, AttributeOption.objects.get(definition=definitions["color"], code="black"))

        bindings = {}
        for index, code in enumerate(("brand", "screen_size", "color", "ram", "storage"), start=1):
            is_dimension = code in {"color", "ram", "storage"}
            form = ProductTypeAttributeForm(data={
                "definition": definitions[code].pk,
                "scope": "variant" if is_dimension else "product",
                "cardinality": "single", "is_required": "on" if is_dimension else "",
                "is_variant_dimension": "on" if is_dimension else "", "sort_order": index,
                "is_filterable": "on", "facet_mode": "range" if code == "screen_size" else "terms",
                "is_searchable": "on", "show_on_storefront": "on", "show_on_product_card": "on" if code in {"brand", "screen_size"} else "",
                "show_in_specifications": "on", "include_in_purchase_snapshot": "on" if is_dimension else "",
                "is_active": "on",
            }, product_type=mobile)
            self.assertTrue(form.is_valid(), form.errors)
            bindings[code] = create_binding(mobile, form)
        for code in ("brand", "color", "ram", "storage"):
            for index, option in enumerate(options[code], start=1):
                form = ProductTypeAttributeOptionForm(data={"option": option.pk, "sort_order": index, "is_active": "on"}, binding=bindings[code])
                self.assertTrue(form.is_valid(), form.errors)
                create_membership(bindings[code], form)

        self.assertEqual(
            set(available_options_for_binding(bindings["brand"]).values_list("code", flat=True)),
            {"apple", "samsung"},
        )
        self.assertEqual(
            set(available_options_for_binding(self.tile.attribute_bindings.get(definition__code="brand")).values_list("code", flat=True)),
            {"atlas"},
        )
        self.assertFalse(run_catalog_framework_audit().blocking_issues)
        self.assertFalse(inspect_tile_catalog(require_complete_generic=True).blocking_issues)


class CatalogSchemaViewTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser("owner", "owner@example.com", "pass")
        self.client.force_login(self.owner)

    def test_schema_navigation_pages_and_product_type_create(self):
        self.assertEqual(self.client.get(reverse("dashboard:schema:home")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:schema:product_type_list")).status_code, 200)
        response = self.client.post(reverse("dashboard:schema:product_type_create"), {
            "code": "mobile", "name": "Mobile", "name_fa": "", "sort_order": 1, "is_active": "on",
        })
        mobile = ProductType.objects.get(code="mobile")
        self.assertRedirects(response, reverse("dashboard:schema:product_type_detail", args=[mobile.pk]))

    def test_category_hierarchy_views(self):
        response = self.client.post(reverse("dashboard:schema:category_create"), {
            "name": "Electronics", "name_fa": "", "slug": "electronics", "parent": "",
            "sort_order": 1, "is_active": "on", "show_in_navigation": "on",
        })
        self.assertRedirects(response, reverse("dashboard:schema:category_list"))
        self.assertContains(self.client.get(reverse("dashboard:schema:category_list")), "Electronics")
