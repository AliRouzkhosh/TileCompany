from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from orders.models import Order, OrderItem
from products.catalog_domain import calculate_variant_combination_signature
from products.currency import Currency
from products.models import (
    AttributeDefinition,
    AttributeOption,
    Category,
    Product,
    ProductAttributeValue,
    ProductType,
    ProductTypeAttribute,
    ProductVariant,
    VariantAttributeValue,
    VariantPrice,
)
from reviews.models import Review, ReviewComment


class SpecificationHierarchyParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.section_stack = []
        self.operational_inside_variant = False

    def handle_starttag(self, tag, attrs):
        if tag != "section":
            return
        attributes = dict(attrs)
        if "data-variant-operational-section" in attributes:
            self.operational_inside_variant = any(
                section.get("data-specification-section") == "variant"
                for section in self.section_stack
            )
        self.section_stack.append(attributes)

    def handle_endtag(self, tag):
        if tag == "section" and self.section_stack:
            self.section_stack.pop()


class StorefrontV4CProductContentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.product_type = ProductType.objects.create(code="content-v4c", name="Content Product")
        cls.category = Category.objects.create(name="Portable Goods", slug="portable-goods-v4c")
        cls.product = Product.objects.create(
            product_type=cls.product_type,
            name="Content Device",
            name_fa="دستگاه محتوایی",
            description="A concise description.\n\nA second paragraph.",
            description_fa="توضیح کوتاه محصول.",
            slug="content-device-v4c",
            sku="CONTENT-V4C",
            is_active=True,
        )
        cls.product.categories.add(cls.category)

        detail_definition = AttributeDefinition.objects.create(
            code="construction_story_v4c", name="Construction story", data_type="text"
        )
        detail_binding = ProductTypeAttribute.objects.create(
            product_type=cls.product_type,
            definition=detail_definition,
            scope="product",
            show_on_storefront=True,
            show_in_specifications=True,
            sort_order=1,
        )
        ProductAttributeValue.objects.create(
            product=cls.product,
            binding=detail_binding,
            text_value="Layered composite with a naturally wrapping long value",
        )

        variant_definition = AttributeDefinition.objects.create(
            code="memory_profile_v4c", name="Memory profile", data_type="option"
        )
        cls.variant_binding = ProductTypeAttribute.objects.create(
            product_type=cls.product_type,
            definition=variant_definition,
            scope="variant",
            is_variant_dimension=True,
            include_in_purchase_snapshot=True,
            show_on_storefront=True,
            show_in_specifications=True,
            sort_order=1,
        )
        option = AttributeOption.objects.create(
            definition=variant_definition, code="extended", name="Extended"
        )
        cls.variant = ProductVariant.objects.create(
            product=cls.product,
            sku="CONTENT-V4C-1",
            price_unit=ProductVariant.PriceUnit.UNIT,
            stock=12,
            is_active=True,
        )
        VariantAttributeValue.objects.create(
            variant=cls.variant, binding=cls.variant_binding, option_value=option
        )
        cls.variant.combination_signature = calculate_variant_combination_signature(cls.variant)
        cls.variant.save(update_fields=["combination_signature"])
        VariantPrice.objects.create(
            variant=cls.variant, currency=Currency.USD, amount=Decimal("199.00")
        )

        cls.related = Product.objects.create(
            product_type=cls.product_type,
            name="Related Device",
            slug="related-device-v4c",
            sku="RELATED-V4C",
            is_active=True,
        )
        cls.related.categories.add(cls.category)
        related_variant = ProductVariant.objects.create(
            product=cls.related,
            sku="RELATED-V4C-1",
            combination_signature="__base__",
            price_unit=ProductVariant.PriceUnit.UNIT,
            stock=4,
            is_active=True,
        )
        VariantPrice.objects.create(
            variant=related_variant, currency=Currency.USD, amount=Decimal("89.00")
        )

        users = []
        for index in range(10):
            user = get_user_model().objects.create_user(
                username=f"reviewer-v4c-{index}", password="password"
            )
            users.append(user)
            Review.objects.create(
                product=cls.product,
                user=user,
                rating=(index % 5) + 1,
                comment=f"Review content {index}",
                is_approved=True,
            )
        cls.owner = users[0]
        cls.review = Review.objects.get(product=cls.product, user=cls.owner)
        delivered_order = Order.objects.create(
            user=cls.owner,
            status=Order.Status.DELIVERED,
            full_name="Review Owner",
            address="Example address",
            city="Example city",
            phone="0000000000",
        )
        OrderItem.objects.create(
            order=delivered_order,
            product=cls.product,
            variant=cls.variant,
            product_name=cls.product.name,
            unit_price=Decimal("199.00"),
            quantity=1,
        )
        ReviewComment.objects.create(
            review=cls.review,
            user=users[1],
            comment="A prepared discussion comment",
            is_approved=True,
        )

    def detail(self, *, language="en"):
        return self.client.get(
            f"/{language}/product/{self.product.slug}/", {"variant": self.variant.pk}
        )

    def source(self, relative_path):
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    def test_tabs_keep_frozen_semantics_hash_keyboard_and_scrollable_rail(self):
        response = self.detail()
        self.assertContains(response, 'role="tablist"', count=1)
        self.assertContains(response, 'role="tab"', count=3)
        self.assertContains(response, 'role="tabpanel"', count=3)
        self.assertContains(response, "data-product-tab-rail")
        self.assertContains(response, "overflow-x-auto")
        self.assertContains(response, "scroll-mt-24")
        tabs = self.source("static/js/tabs.js")
        for contract in (
            'event.key === "ArrowRight"',
            'event.key === "ArrowLeft"',
            'event.key === "Home"',
            'event.key === "End"',
            'window.location.hash.slice(1)',
            "activeButton.scrollIntoView",
            'inline: "nearest"',
        ):
            self.assertIn(contract, tabs)

    def test_description_has_readable_measure_and_restrained_empty_state(self):
        response = self.detail(language="fa")
        self.assertContains(response, "توضیح کوتاه محصول")
        self.assertContains(response, 'data-product-description')
        self.assertContains(response, "max-w-[75ch]")
        empty_type = ProductType.objects.create(code="empty-description-v4c", name="Simple")
        empty_product = Product.objects.create(
            product_type=empty_type,
            name="Empty Description",
            slug="empty-description-v4c",
            sku="EMPTY-DESCRIPTION-V4C",
            is_active=True,
        )
        empty_variant = ProductVariant.objects.create(
            product=empty_product,
            sku="EMPTY-DESCRIPTION-V4C-1",
            combination_signature="__base__",
            stock=1,
            is_active=True,
        )
        VariantPrice.objects.create(
            variant=empty_variant, currency=Currency.USD, amount=Decimal("10.00")
        )
        empty = self.client.get(f"/en/product/{empty_product.slug}/")
        self.assertContains(empty, "data-description-empty")
        self.assertNotContains(empty, "data-product-description")

    def test_specification_order_mobile_stack_and_dynamic_renderer_are_preserved(self):
        response = self.detail()
        html = response.content.decode()
        product_index = html.index('data-specification-section="product"')
        variant_index = html.index('data-specification-section="variant"')
        self.assertLess(product_index, variant_index)
        self.assertContains(response, 'data-product-specification="construction_story_v4c"')
        self.assertContains(response, 'data-variant-specification="memory_profile_v4c"')
        self.assertContains(response, "grid gap-1 py-3")
        self.assertContains(response, "sm:grid-cols-[minmax(0,32%)_minmax(0,68%)]")
        selector = self.source("static/js/variant_selector.js")
        self.assertIn('wrapper.dataset.specificationRow = ""', selector)
        self.assertIn("variant?.detail_specifications", selector)
        self.assertIn("variant?.operational_facts", selector)
        with self.assertNumQueries(0):
            rendered = render_to_string(
                "products/components/product_specs.html",
                {
                    "product_presentation": response.context["product_presentation"],
                    "selected_variant_data": response.context["selected_variant_data"],
                },
            )
        self.assertIn("Product details", rendered)
        self.assertIn("Variant details", rendered)

    def test_operational_facts_are_a_subsection_of_variant_details(self):
        template = self.source("products/templates/products/components/product_specs.html")
        variant_index = template.index('data-specification-section="variant"')
        operational_index = template.index('data-specification-subsection="operational"')
        self.assertLess(variant_index, operational_index)
        self.assertNotIn('data-specification-section="operational"', template)
        self.assertIn('data-variant-operational-section', template)
        self.assertIn('data-variant-operational-rows', template)
        self.assertNotIn('rounded-surface border border-border bg-surface-subtle', template)
        response = self.detail()
        parser = SpecificationHierarchyParser()
        parser.feed(response.content.decode())
        self.assertTrue(parser.operational_inside_variant)
        self.assertContains(response, "data-variant-operational-section hidden")
        self.assertNotIn("data-selected-configuration", template)
        hero_selector = self.source(
            "products/templates/products/components/variant_selector.html"
        )
        self.assertEqual(hero_selector.count("data-selected-configuration{%"), 1)
        selector = self.source("static/js/variant_selector.js")
        self.assertIn("updateVariantDetails(variant)", selector)
        self.assertIn("variant?.detail_specifications", selector)
        self.assertIn("variant?.operational_facts", selector)

    def test_specification_source_is_generic_and_has_no_domain_branching(self):
        specifications = self.source(
            "products/templates/products/components/product_specs.html"
        ).lower()
        source = specifications + self.source("static/js/variant_selector.js").lower()
        self.assertIn("product_presentation.brand_label", specifications)
        self.assertNotIn("product.brand", source)
        for forbidden in (
            '"material"', '"design"', '"usage"', '"color"',
            '"size"', '"finish"', '"ram"', '"storage"', '"cpu"', '"gpu"',
        ):
            self.assertNotIn(forbidden, source)

    def test_reviews_are_compact_semantic_and_keep_comments_and_permissions(self):
        response = self.detail()
        self.assertContains(response, 'class="review-card ', count=10)
        self.assertContains(response, "Verified buyer", count=10)
        self.assertContains(response, "A prepared discussion comment")
        self.assertContains(response, 'data-review-discussion')
        self.assertContains(response, "review-stars")
        self.assertNotContains(response, "★")
        self.assertNotContains(response, "☆")
        self.assertNotContains(response, "review-edit-btn")
        self.client.force_login(self.owner)
        owner_response = self.detail()
        self.assertContains(owner_response, "review-edit-btn")
        self.assertContains(owner_response, "review-delete-btn")
        self.assertContains(owner_response, 'data-review-status="reviewed"')

    def test_review_empty_discussion_and_empty_review_states_are_restrained(self):
        response = self.detail()
        self.assertContains(response, "No replies yet", count=9)
        empty_type = ProductType.objects.create(code="no-reviews-v4c", name="Simple")
        empty_product = Product.objects.create(
            product_type=empty_type,
            name="No Reviews",
            slug="no-reviews-v4c",
            sku="NO-REVIEWS-V4C",
            is_active=True,
        )
        variant = ProductVariant.objects.create(
            product=empty_product,
            sku="NO-REVIEWS-V4C-1",
            combination_signature="__base__",
            stock=1,
            is_active=True,
        )
        VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal("20"))
        empty = self.client.get(f"/en/product/{empty_product.slug}/")
        self.assertContains(empty, "data-reviews-empty")
        self.assertContains(empty, "No reviews yet")

    def test_related_products_keep_shared_card_with_scoped_dense_rail(self):
        response = self.detail()
        self.assertContains(response, "related-product-swiper")
        self.assertContains(response, "data-related-product-slide")
        self.assertContains(response, "data-product-card")
        self.assertContains(response, self.related.get_absolute_url())
        related_source = self.source(
            "products/templates/products/components/related_products.html"
        )
        self.assertIn('products/components/product_card.html', related_source)
        self.assertNotIn("product_card_mobile", related_source)
        css = self.source("theme/static_src/src/styles.css")
        self.assertIn(".related-product-swiper .swiper-slide", css)
        self.assertIn("clamp(11.25rem, 51vw, 12.8125rem)", css)
        self.assertIn("clamp(16.25rem, 21vw, 19rem)", css)
        swiper = self.source("static/js/swiper.js")
        self.assertIn('el.classList.contains("related-product-swiper")', swiper)
        self.assertIn('slidesPerView: "auto"', swiper)

    def test_purchase_affordance_and_content_compensation_contracts_remain(self):
        response = self.detail()
        for hook in (
            "data-purchase-eligibility",
            "data-primary-purchase-action",
            "data-purchase-affordance",
            "data-mobile-purchase-button",
            'id="addToCartForm"',
            "pb-[calc(5.5rem+env(safe-area-inset-bottom))]",
        ):
            self.assertContains(response, hook)
        action = self.source("static/js/product_action.js")
        self.assertIn('document.querySelector("footer")', action)
        self.assertIn("IntersectionObserver", action)

    def test_ten_review_detail_query_count_has_no_n_plus_one_growth(self):
        Review.objects.exclude(pk=self.review.pk).delete()
        self.detail()
        with CaptureQueriesContext(connection) as one_review_queries:
            one_review_response = self.detail()

        for index in range(1, 10):
            reviewer = get_user_model().objects.create_user(
                username=f"bounded-reviewer-v4c-{index}", password="password"
            )
            Review.objects.create(
                product=self.product,
                user=reviewer,
                rating=(index % 5) + 1,
                comment=f"Bounded review content {index}",
                is_approved=True,
            )

        with CaptureQueriesContext(connection) as ten_review_queries:
            ten_review_response = self.detail()

        self.assertEqual(one_review_response.status_code, 200)
        self.assertEqual(ten_review_response.status_code, 200)
        self.assertEqual(len(one_review_queries), len(ten_review_queries))
        self.assertLessEqual(len(ten_review_queries), 37)
