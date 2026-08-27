from decimal import Decimal
from pathlib import Path

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client, RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.translation import override

from dashboard.forms_catalog_schema import AttributeOptionForm
from dashboard.forms_generic_product import GenericProductForm
from dashboard.forms_generic_variant import GenericVariantForm
from dashboard.selectors.generic_products import load_generic_product_form_context
from dashboard.selectors.generic_variants import load_generic_variant_form_context
from dashboard.services_catalog_schema import (
    retire_attribute_option,
    retire_binding,
    retire_membership,
    retire_product_type,
    update_attribute_option,
)
from dashboard.services_generic_product import update_generic_product
from dashboard.services_generic_variant import update_generic_variant
from products.admin import ProductAdmin, ProductVariantAdmin

from .catalog_domain import serialize_combination_signature
from .currency import Currency
from .models import (
    AttributeDefinition,
    AttributeOption,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductTypeAttributeOption,
    ProductVariant,
    VariantAttributeValue,
    VariantPrice,
)
from .publication_readiness import evaluate_product_publication_readiness
from .publication_services import (
    PublicationBlockedError,
    publish_product,
    publish_variant,
    unpublish_product,
    unpublish_variant,
)
from .services import set_variant_price
from .selectors import get_product_by_slug


class ControlledPublicationTests(TestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(override("en"))
        self.mobile = ProductType.objects.create(code="mobile", name="Mobile")
        self.brand_def = AttributeDefinition.objects.create(code="brand", name="Brand", data_type="option")
        self.color_def = AttributeDefinition.objects.create(code="color", name="Color", data_type="option")
        self.ram_def = AttributeDefinition.objects.create(code="ram", name="RAM", data_type="option")
        self.storage_def = AttributeDefinition.objects.create(code="storage", name="Storage", data_type="option")
        self.brand_binding = self._binding(self.brand_def, "product", 10)
        self.color_binding = self._binding(self.color_def, "variant", 10, dimension=True)
        self.ram_binding = self._binding(self.ram_def, "variant", 20, dimension=True)
        self.storage_binding = self._binding(self.storage_def, "variant", 30, dimension=True)
        self.apple = AttributeOption.objects.create(definition=self.brand_def, code="apple", name="Apple")
        self.black = AttributeOption.objects.create(definition=self.color_def, code="black", name="Black")
        self.white = AttributeOption.objects.create(definition=self.color_def, code="white", name="White")
        self.ram12 = AttributeOption.objects.create(definition=self.ram_def, code="12gb", name="12 GB")
        self.storage256 = AttributeOption.objects.create(definition=self.storage_def, code="256gb", name="256 GB")
        self.product = Product.objects.create(
            product_type=self.mobile, name="iPhone 17 Pro", slug="iphone-publication",
            sku="IPHONE-PUBLICATION", is_active=False,
        )
        ProductAttributeValue.objects.create(
            product=self.product, binding=self.brand_binding, option_value=self.apple
        )
        self.variant = self._variant("IPHONE-BLK-12-256", self.black)

    def _binding(self, definition, scope, order, *, dimension=False):
        return ProductTypeAttribute.objects.create(
            product_type=self.mobile, definition=definition, scope=scope,
            is_required=True, is_variant_dimension=dimension,
            include_in_purchase_snapshot=True, sort_order=order,
        )

    def _variant(self, sku, color, *, stock=10):
        signature = serialize_combination_signature((
            ("color", color.code), ("ram", "12gb"), ("storage", "256gb"),
        ))
        variant = ProductVariant.objects.create(
            product=self.product, sku=sku, price_unit="unit", stock=stock,
            low_stock_threshold=2, combination_signature=signature, is_active=False,
        )
        VariantAttributeValue.objects.create(variant=variant, binding=self.color_binding, option_value=color)
        VariantAttributeValue.objects.create(variant=variant, binding=self.ram_binding, option_value=self.ram12)
        VariantAttributeValue.objects.create(variant=variant, binding=self.storage_binding, option_value=self.storage256)
        VariantPrice.objects.create(variant=variant, currency="USD", amount=Decimal("999.00"))
        VariantPrice.objects.create(variant=variant, currency="IRT", amount=Decimal("99900000"))
        return variant

    def _product_form(self, **overrides):
        data = {
            "name": self.product.name, "name_fa": "", "slug": self.product.slug,
            "sku": self.product.sku, "description": "", "description_fa": "",
            "category_ids": [], "primary_category_id": "", "is_featured": "",
            f"attribute_{self.brand_binding.pk}": str(self.apple.pk),
        }
        data.update(overrides)
        context = load_generic_product_form_context(self.mobile, product=self.product)
        return GenericProductForm(
            data, instance=self.product, product_type=self.mobile, schema_context=context
        )

    def _variant_form(self, variant=None, **overrides):
        variant = variant or self.variant
        color = variant.attribute_values.get(binding=self.color_binding).option_value_id
        data = {
            "sku": variant.sku, "price_unit": "unit", "stock": variant.stock,
            "low_stock_threshold": 2, "sort_order": 0,
            f"attribute_{self.color_binding.pk}": color,
            f"attribute_{self.ram_binding.pk}": self.ram12.pk,
            f"attribute_{self.storage_binding.pk}": self.storage256.pk,
            "usd_amount": "999.00", "usd_compare_at_amount": "",
            "irt_amount": "99900000", "irt_compare_at_amount": "",
        }
        data.update(overrides)
        context = load_generic_variant_form_context(self.product, variant=variant)
        return GenericVariantForm(
            data, instance=variant, product=self.product, schema_context=context
        )

    def _publish_all(self):
        publish_variant(self.variant)
        publish_product(self.product)
        self.variant.refresh_from_db()
        self.product.refresh_from_db()

    def test_variant_first_product_second_and_idempotent_transitions(self):
        with self.assertRaises(PublicationBlockedError) as blocked:
            publish_product(self.product)
        self.assertIn("no_active_publication_ready_variant", blocked.exception.issue_codes)
        first = publish_variant(self.variant)
        second = publish_variant(self.variant)
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)
        product_result = publish_product(self.product)
        self.assertTrue(product_result.changed)
        self.assertFalse(publish_product(self.product).changed)
        self.assertTrue(evaluate_product_publication_readiness(self.product).ready)

    def test_unpublish_last_variant_requires_product_first(self):
        self._publish_all()
        with self.assertRaises(PublicationBlockedError) as blocked:
            unpublish_variant(self.variant)
        self.assertEqual(blocked.exception.issue_codes, ("last_active_ready_variant",))
        unpublish_product(self.product)
        self.variant.refresh_from_db()
        self.assertTrue(self.variant.is_active)
        unpublish_variant(self.variant)
        self.variant.refresh_from_db()
        self.assertFalse(self.variant.is_active)

    def test_one_of_two_ready_variants_can_unpublish_but_not_last(self):
        second = self._variant("IPHONE-WHT-12-256", self.white)
        publish_variant(self.variant)
        publish_variant(second)
        publish_product(self.product)
        unpublish_variant(self.variant)
        self.product.refresh_from_db()
        self.assertTrue(self.product.is_active)
        with self.assertRaises(PublicationBlockedError):
            unpublish_variant(second)

    def test_zero_stock_variant_can_publish_with_warning(self):
        ProductVariant.objects.filter(pk=self.variant.pk).update(stock=0)
        result = publish_variant(self.variant)
        self.assertEqual([issue.code for issue in result.warnings], ["zero_stock"])
        publish_product(self.product)

    def test_missing_injected_currency_blocks_publish(self):
        self.variant.prices.filter(currency="IRT").delete()
        self.assertTrue(publish_variant(self.variant, required_currencies=("USD",)).current_active_state)
        unpublish_variant(self.variant)
        with self.assertRaises(PublicationBlockedError) as blocked:
            publish_variant(self.variant)
        self.assertIn("missing_currency_price", blocked.exception.issue_codes)

    def test_active_variant_invalid_dimension_and_price_edits_roll_back(self):
        self._publish_all()
        missing_storage = self._variant_form(**{f"attribute_{self.storage_binding.pk}": ""})
        self.assertTrue(missing_storage.is_valid(), missing_storage.errors)
        with self.assertRaises(PublicationBlockedError):
            update_generic_variant(variant=self.variant, form=missing_storage)
        self.assertTrue(VariantAttributeValue.objects.filter(
            variant=self.variant, binding=self.storage_binding, option_value=self.storage256
        ).exists())
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.combination_signature, "color=black|ram=12gb|storage=256gb")

        missing_irt = self._variant_form(irt_amount="")
        self.assertTrue(missing_irt.is_valid(), missing_irt.errors)
        with self.assertRaises(PublicationBlockedError):
            update_generic_variant(variant=self.variant, form=missing_irt)
        self.assertTrue(self.variant.prices.filter(currency="IRT").exists())

    def test_active_zero_stock_edit_and_product_optional_edit_succeed(self):
        self._publish_all()
        variant_form = self._variant_form(stock=0)
        self.assertTrue(variant_form.is_valid(), variant_form.errors)
        updated = update_generic_variant(variant=self.variant, form=variant_form)
        self.assertTrue(updated.is_active)
        self.assertEqual(updated.stock, 0)
        product_form = self._product_form(name="iPhone 17 Pro Updated")
        self.assertTrue(product_form.is_valid(), product_form.errors)
        updated_product = update_generic_product(product=self.product, form=product_form)
        self.assertTrue(updated_product.is_active)
        self.assertEqual(updated_product.name, "iPhone 17 Pro Updated")

    def test_active_product_required_attribute_removal_rolls_back(self):
        self._publish_all()
        form = self._product_form()
        self.assertTrue(form.is_valid(), form.errors)
        form.cleaned_data[f"attribute_{self.brand_binding.pk}"] = None
        with self.assertRaises((ValidationError, PublicationBlockedError)):
            update_generic_product(product=self.product, form=form)
        self.assertTrue(ProductAttributeValue.objects.filter(
            product=self.product, binding=self.brand_binding, option_value=self.apple
        ).exists())

    def test_schema_invalidation_blocks_and_label_edit_remains_safe(self):
        membership = ProductTypeAttributeOption.objects.create(
            binding=self.color_binding, option=self.black
        )
        self._publish_all()
        with self.assertRaises(ValidationError):
            retire_attribute_option(self.black)
        self.black.refresh_from_db()
        self.assertTrue(self.black.is_active)
        with self.assertRaises(ValidationError):
            retire_membership(membership)
        membership.refresh_from_db()
        self.assertTrue(membership.is_active)
        with self.assertRaises(ValidationError):
            retire_product_type(self.mobile)
        self.mobile.refresh_from_db()
        self.assertTrue(self.mobile.is_active)
        with self.assertRaises(ValidationError):
            retire_binding(self.ram_binding)
        self.ram_binding.refresh_from_db()
        self.assertTrue(self.ram_binding.is_active)

        form = AttributeOptionForm(
            {"code": "black", "name": "Midnight Black", "name_fa": "", "sort_order": 0, "is_active": True},
            instance=self.black,
        )
        self.assertTrue(form.is_valid(), form.errors)
        update_attribute_option(self.black, form)
        self.black.refresh_from_db()
        self.assertEqual(self.black.name, "Midnight Black")

    def test_forms_and_admin_cannot_directly_transition_generic_state(self):
        self.assertNotIn("is_active", self._product_form().fields)
        self.assertNotIn("is_active", self._variant_form().fields)
        request = RequestFactory().get("/admin/")
        request.user = get_user_model().objects.create_superuser(
            username="owner", email="owner@example.com", password="pw"
        )
        self.assertNotIn("is_active", ProductAdmin.list_editable)
        self.assertFalse(ProductAdmin(Product, admin.site).has_change_permission(request, self.product))
        self.assertFalse(ProductVariantAdmin(ProductVariant, admin.site).has_change_permission(request, self.variant))

    def test_dashboard_transitions_are_post_only_and_authorized(self):
        publish_url = reverse("dashboard:products:generic_variant_publish", args=[self.product.pk, self.variant.pk])
        self.assertEqual(self.client.get(publish_url).status_code, 302)
        customer = get_user_model().objects.create_user(username="customer", password="pw")
        self.client.force_login(customer)
        self.assertEqual(self.client.post(publish_url).status_code, 403)
        self.variant.refresh_from_db()
        self.assertFalse(self.variant.is_active)
        call_command("seed_dashboard_roles", verbosity=0)
        staff = get_user_model().objects.create_user(username="staff", password="pw")
        staff.groups.add(Group.objects.get(name="Product Manager"))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(publish_url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(staff)
        self.assertEqual(csrf_client.post(publish_url).status_code, 403)
        self.assertEqual(self.client.post(publish_url).status_code, 302)
        self.variant.refresh_from_db()
        self.assertTrue(self.variant.is_active)

    def test_publication_controls_are_localized(self):
        call_command("seed_dashboard_roles", verbosity=0)
        staff = get_user_model().objects.create_user(username="fa-staff", password="pw")
        staff.groups.add(Group.objects.get(name="Product Manager"))
        self.client.force_login(staff)
        with override("fa"):
            response = self.client.get(
                reverse("dashboard:products:generic_detail", args=[self.product.pk])
            )
        self.assertContains(response, "انتشار محصول")

    def test_zero_dimension_publication_uses_same_services(self):
        simple_type = ProductType.objects.create(code="simple", name="Simple")
        simple = Product.objects.create(
            product_type=simple_type, name="Gift Card", slug="gift-publication",
            sku="GIFT-PUBLICATION", is_active=False,
        )
        base = ProductVariant.objects.create(
            product=simple, sku="GIFT-BASE", price_unit="unit", stock=0,
            combination_signature="__base__", is_active=False,
        )
        VariantPrice.objects.create(variant=base, currency="USD", amount=10)
        VariantPrice.objects.create(variant=base, currency="IRT", amount=500000)
        self.assertTrue(publish_variant(base).current_active_state)
        self.assertTrue(publish_product(simple).current_active_state)

    def test_published_mobile_is_discoverable_and_cart_uses_variant_identity(self):
        self._publish_all()
        selected = get_product_by_slug(self.product.slug, Currency.USD)
        self.assertEqual(selected.pk, self.product.pk)
        detail = self.client.get(reverse("products:product_detail", args=[self.product.slug]))
        self.assertEqual(detail.status_code, 200)
        for text in ("Black", "12 GB", "256 GB"):
            self.assertContains(detail, text)
        catalog = self.client.get(reverse("products:product_list"), {"q": "iPhone 17"})
        self.assertContains(catalog, "iPhone 17 Pro")
        response = self.client.post(
            reverse("orders:cart_add_variant"),
            {"variant_id": self.variant.pk, "quantity": 2},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["cart"], {f"v_{self.variant.pk}": 2})
        cart = self.client.get(reverse("orders:cart_detail"))
        self.assertContains(cart, "Black")
        self.assertContains(cart, "12 GB")
        self.assertContains(cart, "256 GB")
        self.assertContains(cart, "2 items")

    def test_transition_query_counts_are_bounded(self):
        with CaptureQueriesContext(connection) as variant_publish:
            publish_variant(self.variant)
        with CaptureQueriesContext(connection) as product_publish:
            publish_product(self.product)
        with CaptureQueriesContext(connection) as product_unpublish:
            unpublish_product(self.product)
        with CaptureQueriesContext(connection) as variant_unpublish:
            unpublish_variant(self.variant)
        counts = tuple(map(len, (
            variant_publish, product_publish, product_unpublish, variant_unpublish,
        )))
        self.assertTrue(all(count <= 70 for count in counts), counts)

    def test_active_edit_and_schema_query_counts_are_bounded(self):
        self._publish_all()
        variant_form = self._variant_form(stock=9)
        product_form = self._product_form(name="Bounded edit")
        option_form = AttributeOptionForm(
            {"code": "black", "name": "Black renamed", "name_fa": "", "sort_order": 0, "is_active": True},
            instance=self.black,
        )
        self.assertTrue(variant_form.is_valid() and product_form.is_valid() and option_form.is_valid())
        with CaptureQueriesContext(connection) as variant_edit:
            update_generic_variant(variant=self.variant, form=variant_form)
        with CaptureQueriesContext(connection) as product_edit:
            update_generic_product(product=self.product, form=product_form)
        with CaptureQueriesContext(connection) as schema_edit:
            update_attribute_option(self.black, option_form)
        counts = (len(variant_edit), len(product_edit), len(schema_edit))
        self.assertLessEqual(counts[0], 140, counts)
        self.assertLessEqual(counts[1], 80, counts)
        self.assertLessEqual(counts[2], 40, counts)

    def test_standalone_active_price_service_preserves_readiness(self):
        self._publish_all()
        price = set_variant_price(
            variant=self.variant, currency="USD", amount=Decimal("899.00")
        )
        self.assertEqual(price.amount, Decimal("899.00"))
        self.assertTrue(evaluate_product_publication_readiness(self.product).ready)

    def test_only_approved_seed_commands_contain_direct_active_fixture_literals(self):
        command_dir = Path(__file__).parent / "management" / "commands"
        hits = {
            path.name: path.read_text(encoding="utf-8")
            for path in command_dir.glob("*.py")
            if "is_active\": True" in path.read_text(encoding="utf-8")
        }
        demo = hits.pop("seed_demo_catalog.py", None)
        self.assertIsNotNone(demo, "the approved demo fixture should remain explicit in this contract")
        self.assertEqual(set(hits), {"seed_data.py"})
        self.assertIn("sync_tile_product_catalog", hits["seed_data.py"])
        self.assertIn("sync_tile_variant_catalog", hits["seed_data.py"])
        self.assertIn('mode.add_argument("--apply"', demo)
        self.assertIn('settings.APP_ENV == "production"', demo)
        self.assertIn("is_active=False", demo)
        self.assertIn("publish_product", demo)
        self.assertIn("publish_variant", demo)
