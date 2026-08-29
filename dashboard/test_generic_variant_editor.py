from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from products.catalog_framework_audit import run_catalog_framework_audit
from products.catalog_read import read_product_detail
from products.models import (
    AttributeDefinition, AttributeOption, Product, ProductType,
    ProductOperationalChange, ProductTypeAttribute, ProductVariant, VariantAttributeValue, VariantImage, VariantPrice,
)
from products.publication_services import PublicationBlockedError, publish_variant

from .forms_generic_variant import GenericVariantForm
from .selectors.generic_variants import get_generic_variant, load_generic_variant_form_context
from .services_generic_variant import create_generic_variant, update_generic_variant


class GenericVariantEditorTests(TestCase):
    def setUp(self):
        self.mobile = ProductType.objects.create(code="mobile", name="Mobile")
        self.product = Product.objects.create(
            product_type=self.mobile, name="iPhone 17 Pro", slug="iphone-17-pro",
            sku="IP17P", is_active=False,
        )
        self.bindings = {}
        self.options = {}
        self._dimension("color", "Color", (("black", "Black"), ("white", "White")), 10)
        self._dimension("ram", "RAM", (("8gb", "8 GB"), ("12gb", "12 GB")), 20)
        self._dimension("storage", "Storage", (("256gb", "256 GB"), ("512gb", "512 GB")), 30)
        self._spec("network", "Network", "option", 40, options=(("5g", "5G"), ("4g", "4G")))
        self._spec("warranty_months", "Warranty", "integer", 50, unit="months", rules={"min": 0, "max": 36})
        self._spec("weight", "Weight", "decimal", 60, unit="g")
        self._spec("note", "Note", "text", 70, rules={"max_length": 100})
        self._spec("esim", "eSIM", "boolean", 80)

    def _definition(self, code, name, data_type, *, unit="", rules=None):
        return AttributeDefinition.objects.create(
            code=code, name=name, data_type=data_type, unit=unit,
            validation_rules=rules or {},
        )

    def _dimension(self, code, name, options, order):
        definition = self._definition(code, name, "option")
        binding = ProductTypeAttribute.objects.create(
            product_type=self.mobile, definition=definition, scope="variant",
            cardinality="single", is_required=True, is_variant_dimension=True,
            include_in_purchase_snapshot=True, sort_order=order,
        )
        self.bindings[code] = binding
        self.options[code] = {
            option_code: AttributeOption.objects.create(
                definition=definition, code=option_code, name=label, sort_order=index
            )
            for index, (option_code, label) in enumerate(options)
        }

    def _spec(self, code, name, data_type, order, *, unit="", rules=None, options=()):
        definition = self._definition(code, name, data_type, unit=unit, rules=rules)
        binding = ProductTypeAttribute.objects.create(
            product_type=self.mobile, definition=definition, scope="variant",
            cardinality="single", sort_order=order,
        )
        self.bindings[code] = binding
        if options:
            self.options[code] = {
                option_code: AttributeOption.objects.create(
                    definition=definition, code=option_code, name=label, sort_order=index
                )
                for index, (option_code, label) in enumerate(options)
            }

    def _data(self, sku, *, color="black", ram="12gb", storage="256gb", warranty=24, **extra):
        data = {
            "sku": sku, "price_unit": "unit", "stock": extra.get("stock", 10),
            "low_stock_threshold": 2, "sort_order": 0,
            f"attribute_{self.bindings['color'].pk}": self.options["color"][color].pk if color else "",
            f"attribute_{self.bindings['ram'].pk}": self.options["ram"][ram].pk if ram else "",
            f"attribute_{self.bindings['storage'].pk}": self.options["storage"][storage].pk if storage else "",
            f"attribute_{self.bindings['network'].pk}": self.options["network"]["5g"].pk,
            f"attribute_{self.bindings['warranty_months'].pk}": warranty,
            f"attribute_{self.bindings['weight'].pk}": "199.000000",
            f"attribute_{self.bindings['note'].pk}": "Flagship",
            f"attribute_{self.bindings['esim'].pk}": "false",
            "usd_amount": "999.00", "usd_compare_at_amount": "1099.00",
            "irt_amount": "99900000", "irt_compare_at_amount": "109900000",
        }
        return data

    def _form(self, data, *, variant=None):
        context = load_generic_variant_form_context(self.product, variant=variant)
        return GenericVariantForm(data, instance=variant, product=self.product, schema_context=context)

    def _create(self, sku, **kwargs):
        form = self._form(self._data(sku, **kwargs))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        return create_generic_variant(product=self.product, form=form)

    def test_mobile_variants_use_generic_values_signatures_prices_and_no_tile_fields(self):
        first = self._create("IP17P-BLK-12-256")
        second = self._create("IP17P-WHT-12-512", color="white", storage="512gb", warranty=12, stock=5)
        self.assertEqual(first.combination_signature, "color=black|ram=12gb|storage=256gb")
        self.assertEqual(second.combination_signature, "color=white|ram=12gb|storage=512gb")
        self.assertEqual(first.price_unit, ProductVariant.PriceUnit.UNIT)
        self.assertFalse(first.is_active)
        for field in ("color_id", "size_id", "finish_id", "length_mm", "width_mm", "thickness_mm", "tiles_per_box", "coverage_per_box_m2"):
            self.assertIsNone(getattr(first, field))
        self.assertEqual(first.attribute_values.count(), 8)
        self.assertEqual(first.prices.count(), 2)
        self.assertEqual(first.attribute_values.get(binding=self.bindings["esim"]).boolean_value, False)
        detail = read_product_detail(self.product)
        self.assertEqual(detail.variants[0].attributes["storage"], "256gb")
        self.assertFalse(run_catalog_framework_audit().blocking_issues)

    def test_duplicate_combination_rolls_back_everything(self):
        self._create("IP17P-BLK-12-256")
        before_values = VariantAttributeValue.objects.count()
        form = self._form(self._data("DIFFERENT-SKU"))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        with self.assertRaisesMessage(ValidationError, "already uses"):
            create_generic_variant(product=self.product, form=form)
        self.assertFalse(ProductVariant.objects.filter(sku="DIFFERENT-SKU").exists())
        self.assertEqual(VariantAttributeValue.objects.count(), before_values)

    def test_edit_replaces_specs_signature_and_preserves_sku_stock_and_prices(self):
        variant = self._create("IP17P-BLK-12-256")
        price_ids = list(variant.prices.order_by("currency").values_list("id", flat=True))
        form = self._form(self._data(variant.sku, storage="512gb", warranty="", stock=10), variant=variant)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        updated = update_generic_variant(variant=variant, form=form)
        self.assertEqual(updated.sku, "IP17P-BLK-12-256")
        self.assertEqual(updated.stock, 10)
        self.assertEqual(updated.combination_signature, "color=black|ram=12gb|storage=512gb")
        self.assertFalse(updated.attribute_values.filter(binding=self.bindings["warranty_months"]).exists())
        self.assertEqual(list(updated.prices.order_by("currency").values_list("id", flat=True)), price_ids)

    def test_generic_manual_operational_edit_creates_one_combined_actor_audit_event(self):
        call_command("seed_dashboard_roles", verbosity=0)
        actor = get_user_model().objects.create_user("generic-manager")
        actor.groups.add(Group.objects.get(name="Product Manager"))
        variant = self._create("IP17P-AUDIT")
        form = self._form(self._data(variant.sku, stock=9), variant=variant)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        update_generic_variant(variant=variant, form=form, user=actor)
        event = ProductOperationalChange.objects.get(variant=variant)
        self.assertEqual(event.actor, actor)
        self.assertEqual(event.source, ProductOperationalChange.Source.MANUAL)
        self.assertEqual(event.changed_fields, ["stock"])

    def test_incomplete_draft_has_null_signature_then_can_be_completed(self):
        variant = self._create("IP17P-INCOMPLETE", ram=None, storage=None)
        self.assertIsNone(variant.combination_signature)
        self.assertEqual(
            set(variant.attribute_values.filter(binding__is_variant_dimension=True).values_list("binding__definition__code", flat=True)),
            {"color"},
        )
        form = self._form(self._data(variant.sku), variant=variant)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        variant = update_generic_variant(variant=variant, form=form)
        self.assertEqual(variant.combination_signature, "color=black|ram=12gb|storage=256gb")
        self.assertFalse(variant.is_active)

    def test_retired_current_option_is_readable_and_publication_is_blocked(self):
        variant = self._create("IP17P-RETIRED")
        black = self.options["color"]["black"]
        black.is_active = False
        black.save(update_fields=["is_active"])
        context = load_generic_variant_form_context(self.product, variant=variant)
        self.assertIn(black.pk, context.unavailable_current_option_ids)
        data = self._data(variant.sku)
        data["is_active"] = "on"
        form = GenericVariantForm(data, instance=variant, product=self.product, schema_context=context)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn("is_active", form.fields)
        with self.assertRaises(PublicationBlockedError):
            publish_variant(variant)

    def test_zero_dimension_base_variant_and_duplicate(self):
        service_type = ProductType.objects.create(code="service", name="Service")
        service = Product.objects.create(product_type=service_type, name="Setup", slug="setup", sku="SETUP", is_active=False)
        context = load_generic_variant_form_context(service)
        data = {"sku": "SETUP-BASE", "price_unit": "unit", "stock": 0, "low_stock_threshold": 1, "sort_order": 0}
        form = GenericVariantForm(data, product=service, schema_context=context)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        base = create_generic_variant(product=service, form=form)
        self.assertEqual(base.combination_signature, "__base__")
        second_form = GenericVariantForm({**data, "sku": "SETUP-BASE-2"}, product=service, schema_context=context)
        self.assertTrue(second_form.is_valid(), second_form.errors.as_json())
        with self.assertRaises(ValidationError):
            create_generic_variant(product=service, form=second_form)

    def test_tile_rejected_and_context_queries_are_bounded(self):
        with self.assertNumQueries(3):
            context = load_generic_variant_form_context(self.product)
        with self.assertNumQueries(0):
            GenericVariantForm(self._data("QUERY"), product=self.product, schema_context=context)
        tile = ProductType.objects.create(code="tile", name="Tile")
        tile_product = Product.objects.create(product_type=tile, name="Tile", slug="tile", sku="TILE", is_active=False)
        tile_context = load_generic_variant_form_context(tile_product)
        form = GenericVariantForm({"sku": "TILE-GENERIC", "price_unit": "box", "stock": 0, "low_stock_threshold": 1, "sort_order": 0}, product=tile_product, schema_context=tile_context)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        with self.assertRaises(ValidationError):
            create_generic_variant(product=tile_product, form=form)

    def test_transaction_failure_rolls_back_variant_values_and_prices(self):
        form = self._form(self._data("ROLLBACK"))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        with self.assertRaisesMessage(RuntimeError, "stop"):
            create_generic_variant(product=self.product, form=form, failure_hook=lambda: (_ for _ in ()).throw(RuntimeError("stop")))
        self.assertFalse(ProductVariant.objects.filter(sku="ROLLBACK").exists())
        self.assertFalse(VariantPrice.objects.exists())

    def test_routes_keep_generic_and_tile_authority_separate(self):
        call_command("seed_dashboard_roles", verbosity=0)
        user = get_user_model().objects.create_user(username="catalog-staff", password="secret")
        user.groups.add(Group.objects.get(name="Product Manager"))
        self.client.force_login(user)
        detail = self.client.get(reverse("dashboard:products:generic_detail", args=[self.product.pk]))
        generic_catalog = self.client.get(reverse("dashboard:products:generic_variant_catalog", args=[self.product.pk]))
        generic_create = self.client.get(reverse("dashboard:products:generic_variant_create", args=[self.product.pk]))
        self.assertEqual(generic_catalog.status_code, 200)
        self.assertEqual(generic_create.status_code, 200)
        for response in (detail, generic_catalog, generic_create):
            self.assertNotContains(response, "until F2C")

        variant = self._create("IP17P-REDIRECT")
        self.assertRedirects(
            self.client.get(reverse("dashboard:products:variants:catalog", args=[self.product.pk])),
            reverse("dashboard:products:generic_variant_catalog", args=[self.product.pk]),
        )
        legacy_for_generic = self.client.get(
            reverse("dashboard:products:variants:create", args=[self.product.pk])
        )
        self.assertRedirects(legacy_for_generic, reverse("dashboard:products:generic_variant_create", args=[self.product.pk]))
        self.assertRedirects(
            self.client.get(reverse("dashboard:products:variants:edit", args=[self.product.pk, variant.pk])),
            reverse("dashboard:products:generic_variant_edit", args=[self.product.pk, variant.pk]),
        )

        image = VariantImage.objects.create(variant=variant, image="products/variants/generic.jpg")
        gallery_delete = self.client.post(
            reverse("dashboard:products:variants:gallery_delete", args=[self.product.pk, variant.pk, image.pk]),
            follow=True,
        )
        self.assertEqual(gallery_delete.redirect_chain[-1][0], reverse(
            "dashboard:products:generic_variant_edit", args=[self.product.pk, variant.pk],
        ))
        self.assertTrue(VariantImage.objects.filter(pk=image.pk).exists())
        self.assertContains(gallery_delete, "Removing existing variant images is not currently supported for this product type.")
        self.assertNotContains(gallery_delete, "F2C")

        tile = ProductType.objects.create(code="tile", name="Tile")
        tile_product = Product.objects.create(product_type=tile, name="Tile", slug="tile-route", sku="TILE-ROUTE", is_active=False)
        generic_for_tile = self.client.get(
            reverse("dashboard:products:generic_variant_create", args=[tile_product.pk])
        )
        self.assertRedirects(
            generic_for_tile,
            reverse("dashboard:products:variants:catalog", args=[tile_product.pk]),
        )
