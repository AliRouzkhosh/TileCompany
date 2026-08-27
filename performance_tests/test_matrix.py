"""Extended PostgreSQL route/scaling matrix for P2.7B-1F-B1."""

import os
import unittest
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone, translation

from accounts.models import Address
from accounts.tests import AccountShellTests
from blog.models import Article, ArticleStatus, BlogCategory, BlogTag, Comment, SitePageKey
from blog.tests import PublicEditorialExperienceTests
from dashboard.test_blog import BlogDashboardTests
from dashboard.test_site_pages import SitePageDashboardTests
from dashboard.tests import DashboardOrderDetailLocalizationTests, DashboardSummaryCardTests, DashboardVariantWorkflowTests
from orders.models import Order
from orders.test_storefront_v5a import StorefrontV5ACartTests
from orders.test_storefront_v5c import StorefrontV5CCheckoutStepTests
from orders.test_storefront_v6d1 import CustomerOrderDetailV6D1Tests
from orders.tests import CustomerOrderListTests
from products.currency import Currency
from products.models import (
    Brand,
    Category,
    HomepageEditorialSelection,
    Product,
    ProductImage,
    ProductType,
    ProductVariant,
    VariantImage,
    VariantPrice,
)
from products.test_storefront_v3b import StorefrontV3BDesktopCatalogTests
from reviews.models import Review
from support.models import HelpArticle, TicketCategory, TicketReply
from support.tests import CustomerTicketDiscoveryTests, HelpCenterLocalizationTests

from .queries import capture_route


def measure(testcase, surface, url, params=None):
    response, result = capture_route(surface, lambda: testcase.client.get(url, params or {}))
    testcase.assertEqual(response.status_code, 200)
    return result


class HomepageMatrixTests(TestCase):
    def setUp(self):
        self.article = Article.objects.create(
            title="Homepage automatic article",
            title_fa="مقاله خودکار خانه",
            slug="homepage-automatic-article",
            content="<p>Fixture</p>",
            content_fa="<p>نمونه</p>",
            status=ArticleStatus.PUBLISHED,
            published_at=timezone.now(),
        )

    def test_measure_homepage_matrix(self):
        self.assertEqual(connection.vendor, "postgresql")
        automatic_en = measure(self, "homepage_en_automatic", "/en/")
        automatic_fa = measure(self, "homepage_fa_automatic", "/fa/")
        HomepageEditorialSelection.objects.create(article=self.article)
        explicit_en = measure(self, "homepage_en_explicit", "/en/")
        explicit_fa = measure(self, "homepage_fa_explicit", "/fa/")
        self.assertEqual(automatic_en.query_count, automatic_fa.query_count)
        self.assertEqual(explicit_en.query_count, explicit_fa.query_count)
        for result in (automatic_en, automatic_fa, explicit_en, explicit_fa):
            self.assertFalse(any(
                count > 1 and any(table in shape for table in (
                    "products_category", "products_attributeoption", "products_brand",
                ))
                for shape, count in result.duplicate_shapes
            ))


class CatalogMatrixTests(StorefrontV3BDesktopCatalogTests):
    def test_measure_catalog_matrix(self):
        brand = Brand.objects.create(name="Matrix Brand", name_fa="برند ماتریس", slug="matrix-brand")
        self.product.brand = brand
        self.product.name_fa = "لوازم عمومی"
        self.product.save(update_fields=["brand", "name_fa"])
        price = VariantPrice.objects.get(variant__product=self.product, currency=Currency.USD)
        price.compare_at_amount = Decimal("100")
        price.save(update_fields=["compare_at_amount"])
        small = measure(self, "catalog_small", "/en/shop/")
        for index in range(15):
            product = Product.objects.create(
                product_type=self.product_type,
                brand=brand,
                name=f"Matrix Product {index:02d}",
                name_fa=f"محصول ماتریس {index:02d}",
                slug=f"matrix-product-{index}",
                sku=f"MATRIX-{index}",
                is_active=True,
            )
            product.categories.add(self.category)
            variant = ProductVariant.objects.create(
                product=product,
                sku=f"MATRIX-{index}-A",
                stock=2,
                is_active=True,
                combination_signature="__base__",
                price_unit=ProductVariant.PriceUnit.UNIT,
            )
            VariantPrice.objects.create(
                variant=variant,
                currency=Currency.USD,
                amount=Decimal(20 + index),
                compare_at_amount=Decimal(50 + index) if index % 3 == 0 else None,
            )
        routes = (
            ("catalog_en_default", "/en/shop/", {}),
            ("catalog_fa_default", "/fa/shop/", {}),
            ("catalog_category", "/en/shop/", {"categories": self.category.slug}),
            ("catalog_brand", "/en/shop/", {"brand": brand.slug}),
            ("catalog_attribute", "/en/shop/", {"attr_maker": self.used_option.code}),
            ("catalog_on_sale", "/en/shop/", {"on_sale": "1"}),
            ("catalog_price_sort", "/en/shop/", {"sort": "price_asc"}),
            ("catalog_page_2", "/en/shop/", {"page": "2"}),
            ("catalog_search", "/en/shop/", {"q": "Matrix"}),
        )
        measurements = {}
        for surface, url, params in routes:
            result = measure(self, surface, url, params)
            measurements[surface] = result
            if surface == "catalog_en_default":
                print(f"PERFORMANCE_SCALING surface=catalog small={small.query_count} full={result.query_count}")
        warmed = [
            measure(self, f"catalog_on_sale_warm_{index}", "/en/shop/", {"on_sale": "1"})
            for index in range(1, 4)
        ]
        print(
            "PERFORMANCE_TIMING surface=catalog_on_sale_warmed local_docker_ms="
            + ",".join(f"{result.elapsed_ms:.1f}" for result in warmed)
        )
        default = measurements["catalog_en_default"]
        on_sale = measurements["catalog_on_sale"]
        on_sale_sql = "\n".join(sql for sql, _duration in on_sale.query_details)
        self.assertIn('JOIN "products_variantprice"', on_sale_sql)
        self.assertNotIn(
            'SELECT U0."compare_at_amount"',
            on_sale_sql,
            "on_sale qualification must not rebuild scalar price subqueries inside every facet",
        )
        print(
            "PERFORMANCE_TIMING_COMPARE "
            f"default_wall_ms={default.elapsed_ms:.1f} "
            f"default_db_ms={sum(duration for _sql, duration in default.query_details):.3f} "
            f"on_sale_wall_ms={on_sale.elapsed_ms:.1f} "
            f"on_sale_db_ms={sum(duration for _sql, duration in on_sale.query_details):.3f}"
        )
        if os.environ.get("PERFORMANCE_EXPLAIN", "false").lower() in {"1", "true", "yes"}:
            selects = sorted(
                (
                    (duration, sql)
                    for sql, duration in on_sale.query_details
                    if sql.lstrip().upper().startswith("SELECT")
                ),
                reverse=True,
            )[:3]
            with connection.cursor() as cursor:
                for rank, (duration, sql) in enumerate(selects, start=1):
                    cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql)
                    print(
                        f"PERFORMANCE_EXPLAIN rank={rank} captured_db_ms={duration:.3f}\n"
                        + "\n".join(row[0] for row in cursor.fetchall())
                    )
                for table in ("products_product", "products_productvariant", "products_variantprice"):
                    cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
                    print(f"PERFORMANCE_ROW_COUNT table={table} rows={cursor.fetchone()[0]}")
                cursor.execute(
                    "SELECT tablename, indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = current_schema() AND tablename IN "
                    "('products_product', 'products_productvariant', 'products_variantprice') "
                    "ORDER BY tablename, indexname"
                )
                for table, name, definition in cursor.fetchall():
                    print(f"PERFORMANCE_INDEX table={table} name={name} definition={definition}")
        self.assertTrue(all(result.query_count == measurements["catalog_on_sale"].query_count for result in warmed))


class ProductDetailMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.category = Category.objects.create(name="Installation", slug="performance-installation")
        cls.brand = Brand.objects.create(name="Performance Brand", slug="performance-brand")
        cls.products = []
        for code in ("ceramic-tile", "tile-adhesive", "grout", "installation-additive"):
            product_type = ProductType.objects.create(code=code, name=code.replace("-", " ").title())
            product = Product.objects.create(
                product_type=product_type,
                brand=cls.brand,
                name=f"Performance {code}",
                slug=f"performance-{code}",
                sku=f"PERF-{code}",
                is_active=True,
            )
            product.categories.add(cls.category)
            variant = ProductVariant.objects.create(
                product=product,
                sku=f"PERF-{code}-1",
                stock=10,
                is_active=True,
                combination_signature="__base__",
                price_unit=ProductVariant.PriceUnit.UNIT,
            )
            VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal("25"))
            cls.products.append(product)

    def test_measure_product_detail_types_and_scaling(self):
        self.assertEqual(connection.vendor, "postgresql")
        self.client.get("/en/")
        for product in self.products:
            measure(self, f"product_detail_{product.product_type.code}", product.get_absolute_url())

        target = self.products[0]
        minimal = measure(self, "product_detail_minimal_related", target.get_absolute_url())
        for index in range(5):
            ProductImage.objects.create(product=target, image=f"products/gallery/perf-{index}.jpg", sort_order=index)
            reviewer = get_user_model().objects.create_user(f"performance-reviewer-{index}")
            Review.objects.create(product=target, user=reviewer, rating=5, comment="Measured", is_approved=True)
            related = Product.objects.create(
                product_type=target.product_type,
                brand=self.brand,
                name=f"Related {index}",
                slug=f"performance-related-{index}",
                sku=f"PERF-RELATED-{index}",
                is_active=True,
            )
            related.categories.add(self.category)
            related_variant = ProductVariant.objects.create(
                product=related,
                sku=f"PERF-RELATED-{index}-1",
                stock=2,
                is_active=True,
                combination_signature="__base__",
            )
            VariantPrice.objects.create(variant=related_variant, currency=Currency.USD, amount=Decimal("20"))
        populated = measure(self, "product_detail_populated_related", target.get_absolute_url())
        self.assertLessEqual(populated.query_count, minimal.query_count + 1)
        self.assertFalse(
            any("FROM \"products_brand\" WHERE \"products_brand\".\"id\"" in shape for shape, _count in populated.duplicate_shapes),
            "related product cards must not dereference brand once per card",
        )
        print(f"PERFORMANCE_SCALING surface=product_detail small={minimal.query_count} full={populated.query_count}")


class ArticleMatrixTests(PublicEditorialExperienceTests):
    def test_measure_article_index_matrix(self):
        url = reverse("blog:article_list")
        full = measure(self, "articles_9", url)
        routes = (
            ("articles_page_2", {"page": "2"}),
            ("articles_category", {"category": self.category.slug}),
            ("articles_tag", {"tag": self.tag.slug}),
            ("articles_search_en", {"q": "Editorial"}),
            ("articles_search_fa", {"q": "مقاله"}),
            ("articles_newest", {"sort": "newest"}),
            ("articles_oldest", {"sort": "oldest"}),
            ("articles_title_en", {"sort": "title-asc"}),
        )
        for surface, params in routes:
            measure(self, surface, url, params)
        with translation.override("fa"):
            measure(self, "articles_title_fa", "/fa/articles/", {"sort": "title-asc"})
        Article.objects.exclude(pk=self.articles[0].pk).delete()
        small = measure(self, "articles_1", url)
        print(f"PERFORMANCE_SCALING surface=articles small={small.query_count} full={full.query_count}")

    def test_measure_article_detail_scaling(self):
        article = self.articles[0]
        article.tags.clear()
        minimal = measure(self, "article_detail_minimal", article.get_absolute_url())
        tags = [BlogTag.objects.create(name=f"Matrix Tag {i}", slug=f"matrix-tag-{i}") for i in range(5)]
        article.tags.add(*tags)
        related_type = ProductType.objects.create(code="article-related", name="Article Related")
        one_related = None
        for index in range(5):
            Article.objects.create(
                title=f"Related article {index}",
                slug=f"matrix-related-article-{index}",
                content="<p>Related</p>",
                category=self.category,
                status=ArticleStatus.PUBLISHED,
                published_at=timezone.now() - timedelta(days=index + 1),
            )
            product = Product.objects.create(
                product_type=related_type,
                name=f"Article product {index}",
                slug=f"article-product-{index}",
                sku=f"ARTICLE-PRODUCT-{index}",
                is_active=True,
            )
            related_variant = ProductVariant.objects.create(
                product=product,
                sku=f"ARTICLE-PRODUCT-{index}-1",
                stock=2,
                is_active=True,
                combination_signature="__base__",
            )
            VariantPrice.objects.create(variant=related_variant, currency=Currency.USD, amount=Decimal("20"))
            article.related_products.add(product)
            user = get_user_model().objects.create_user(f"article-commenter-{index}")
            Comment.objects.create(article=article, user=user, body="Public comment", is_approved=True)
            if index == 0:
                one_related = measure(self, "article_detail_1_related", article.get_absolute_url())
        populated = measure(self, "article_detail_populated", article.get_absolute_url())
        print(f"PERFORMANCE_SCALING surface=article_detail small={minimal.query_count} full={populated.query_count}")
        print(f"PERFORMANCE_SCALING surface=article_detail_related_1_vs_5 small={one_related.query_count} full={populated.query_count}")


class HelpMatrixTests(HelpCenterLocalizationTests):
    def test_measure_help_matrix(self):
        url = reverse("support:help_list")
        for surface, path, params in (
            ("help_index", url, {}),
            ("help_category", url, {"category": self.category.slug}),
            ("help_search_en", url, {"q": "track"}),
            ("help_search_fa", "/fa/support/help/", {"q": "سفارش"}),
        ):
            measure(self, surface, path, params)
        minimal = measure(self, "help_detail_minimal", self.article.get_absolute_url() if hasattr(self.article, "get_absolute_url") else f"/en/support/help/{self.article.slug}/")
        for index in range(8):
            HelpArticle.objects.create(
                category=self.category,
                title=f"Related guide {index}",
                title_fa=f"راهنمای مرتبط {index}",
                slug=f"related-guide-{index}",
                content="Guide",
                content_fa="راهنما",
                is_published=True,
            )
        populated = measure(self, "help_detail_related", f"/en/support/help/{self.article.slug}/")
        print(f"PERFORMANCE_SCALING surface=help_detail small={minimal.query_count} full={populated.query_count}")


class AccountMatrixTests(AccountShellTests):
    def test_measure_account_matrix(self):
        self.assertEqual(connection.vendor, "postgresql")
        Address.objects.create(
            user=self.user,
            label="Matrix",
            first_name="Performance",
            last_name="Customer",
            phone="0",
            country="Iran",
            province="Tehran",
            city="Tehran",
            street_address="Fixture",
            postal_code="0",
        )
        measurements = {}
        for surface, url in (
            ("account_overview", reverse("accounts:overview")),
            ("account_profile_en", reverse("accounts:profile")),
            ("account_profile_fa", "/fa/accounts/profile/"),
            ("account_addresses", reverse("accounts:address_list")),
            ("account_wishlist", reverse("products:wishlist")),
            ("account_notifications", reverse("notifications:center")),
            ("account_tickets", reverse("support:ticket_list")),
        ):
            measurements[surface] = measure(self, surface, url)
        for surface in ("account_profile_en", "account_profile_fa"):
            self.assertFalse(
                any("accounts_phoneidentity" in shape for shape, _count in measurements[surface].duplicate_shapes),
                f"{surface} must query PhoneIdentity once",
            )
        self.assertEqual(
            measurements["account_profile_en"].query_count,
            measurements["account_profile_fa"].query_count,
        )


class CartMatrixTests(StorefrontV5ACartTests):
    def test_measure_cart_scaling(self):
        variants = []
        for index in range(5):
            product_type = ProductType.objects.create(code=f"cart-type-{index}", name=f"Cart Type {index}")
            product = Product.objects.create(
                product_type=product_type,
                name=f"Cart Product {index}",
                slug=f"cart-product-{index}",
                sku=f"CART-PRODUCT-{index}",
                is_active=True,
            )
            variant = ProductVariant.objects.create(
                product=product,
                sku=f"CART-PRODUCT-{index}-1",
                stock=4,
                is_active=True,
                combination_signature="__base__",
                price_unit=ProductVariant.PriceUnit.UNIT,
            )
            VariantPrice.objects.create(
                variant=variant,
                currency=Currency.USD,
                amount=Decimal(15 + index),
                compare_at_amount=Decimal(25 + index) if index == 0 else None,
            )
            variants.append(variant)
        self._set_cart(((variants[0], 1),))
        small = measure(self, "cart_1_line", reverse("orders:cart_detail"))
        self._set_cart(tuple((variant, 1) for variant in variants))
        full = measure(self, "cart_5_lines", reverse("orders:cart_detail"))
        print(f"PERFORMANCE_SCALING surface=cart small={small.query_count} full={full.query_count}")


class CheckoutMatrixTests(StorefrontV5CCheckoutStepTests):
    def test_measure_checkout_matrix(self):
        for index in range(3):
            product_type = ProductType.objects.create(code=f"checkout-extra-{index}", name=f"Checkout Extra {index}")
            product = Product.objects.create(
                product_type=product_type,
                name=f"Checkout Extra {index}",
                slug=f"checkout-extra-{index}",
                sku=f"CHECKOUT-EXTRA-{index}",
                is_active=True,
            )
            variant = ProductVariant.objects.create(
                product=product,
                sku=f"CHECKOUT-EXTRA-{index}-1",
                stock=5,
                is_active=True,
                combination_signature="__base__",
            )
            VariantPrice.objects.create(variant=variant, currency=Currency.USD, amount=Decimal("20"))
            self.variants.append(variant)

        self._set_checkout(lines=1)
        for step in ("shipping", "payment", "review"):
            measure(self, f"checkout_{step}_1_line_1_address", reverse(f"orders:checkout_{step}"))

        for index in range(7):
            Address.objects.create(
                user=self.user,
                label=f"Extra {index}",
                first_name="Extra",
                last_name=str(index),
                phone="0",
                country="Iran",
                province="Tehran",
                city="Tehran",
                street_address=f"Extra {index}",
                postal_code=str(index),
            )
        for lines in (1, 5):
            self._set_checkout(lines=lines)
            for step in ("shipping", "payment", "review"):
                measure(self, f"checkout_{step}_{lines}_lines_8_addresses", reverse(f"orders:checkout_{step}"))


class OrderListMatrixTests(CustomerOrderListTests):
    def test_measure_order_list_matrix(self):
        for index in range(15):
            self.order(status=Order.Status.CONFIRMED, number=index + 1)
        for index in range(3):
            self.order(status=Order.Status.DELIVERED, paid=True, number=101 + index)
            self.order(status=Order.Status.CANCELLED, number=201 + index)
            refund_order = self.order(status=Order.Status.DELIVERED, paid=True, number=301 + index)
            refund_order.refund_status = Order.RefundStatus.REQUESTED
            refund_order.refund_reason = "Measured fixture"
            refund_order.refund_requested_at = timezone.now()
            refund_order.save(update_fields=["refund_status", "refund_reason", "refund_requested_at"])
        for surface, params in (
            ("orders_current", {"status": "current"}),
            ("orders_delivered", {"status": "delivered"}),
            ("orders_cancelled", {"status": "cancelled"}),
            ("orders_refunds", {"status": "refund"}),
            ("orders_page_2", {"status": "current", "page": "2"}),
        ):
            measure(self, surface, reverse("orders:order_list"), params)


class OrderDetailMatrixTests(CustomerOrderDetailV6D1Tests):
    def test_measure_order_detail_scaling(self):
        small_order = self.order(paid=True)
        self.item(small_order)
        small = measure(self, "order_detail_1_item", reverse("orders:order_detail", args=[small_order.pk]))
        large_order = self.order(status=Order.Status.DELIVERED, paid=True)
        for index in range(12):
            self.item(large_order, f"Measured item {index}")
        full = measure(self, "order_detail_12_items", reverse("orders:order_detail", args=[large_order.pk]))
        refund_order = self.order(status=Order.Status.DELIVERED, paid=True)
        refund_order.refund_status = Order.RefundStatus.REQUESTED
        refund_order.refund_reason = "Measured fixture"
        refund_order.refund_requested_at = timezone.now()
        refund_order.save(update_fields=["refund_status", "refund_reason", "refund_requested_at"])
        self.item(refund_order)
        measure(self, "order_detail_refund", reverse("orders:order_detail", args=[refund_order.pk]))
        print(f"PERFORMANCE_SCALING surface=order_detail small={small.query_count} full={full.query_count}")


class DashboardHomeMatrixTests(DashboardSummaryCardTests):
    def test_measure_dashboard_home(self):
        self.assertEqual(connection.vendor, "postgresql")
        result = measure(self, "dashboard_home", reverse("dashboard:home"))
        self.assertLessEqual(result.query_count, 37)


class DashboardProductMatrixTests(DashboardVariantWorkflowTests):
    def test_measure_dashboard_products(self):
        self.client.force_login(self.user)
        small = measure(self, "dashboard_products_small", reverse("dashboard:products:catalog"))
        for index in range(25):
            Product.objects.create(name=f"Dashboard Matrix {index}", slug=f"dashboard-matrix-{index}", sku=f"DASH-MATRIX-{index}")
        full = measure(self, "dashboard_products_full", reverse("dashboard:products:catalog"))
        print(f"PERFORMANCE_SCALING surface=dashboard_products small={small.query_count} full={full.query_count}")
        measure(self, "dashboard_product_detail", reverse("dashboard:products:detail", args=[self.product.pk]))
        measure(self, "dashboard_variants", reverse("dashboard:products:variants:catalog", args=[self.product.pk]))


class DashboardOrderMatrixTests(DashboardOrderDetailLocalizationTests):
    def test_measure_dashboard_orders(self):
        self.assertEqual(connection.vendor, "postgresql")
        measurements = {}
        for surface, url in (
            ("dashboard_all_orders", reverse("dashboard:all_orders")),
            ("dashboard_attention", reverse("dashboard:attention_queue")),
            ("dashboard_fulfillment", reverse("dashboard:fulfillment_queue")),
            ("dashboard_refunds", reverse("dashboard:refund_queue")),
            ("dashboard_order_detail", reverse("dashboard:order_detail", args=[self.order.pk])),
        ):
            measurements[surface] = measure(self, surface, url)
        for surface in (
            "dashboard_all_orders", "dashboard_attention", "dashboard_fulfillment", "dashboard_refunds",
        ):
            self.assertLess(measurements[surface].query_count, 36 if surface == "dashboard_attention" else 40)


class SitePageMatrixTests(SitePageDashboardTests):
    def test_measure_site_pages(self):
        page = self.page(SitePageKey.ABOUT)
        page.title = "About"
        page.title_fa = "درباره"
        page.content = "<p>About fixture</p>"
        page.content_fa = "<p>نمونه درباره</p>"
        page.is_published = True
        page.save()
        measure(self, "sitepage_public", reverse("sitepages:about"))
        self.login()
        measure(self, "dashboard_sitepages", reverse("dashboard:blog:pages"))
        measure(self, "dashboard_sitepage_edit", reverse("dashboard:blog:page_edit", args=[page.pk]))
        measure(self, "dashboard_sitepage_preview", reverse("dashboard:blog:page_preview", args=[page.pk]))


class DashboardContentMatrixTests(BlogDashboardTests):
    def test_measure_dashboard_content(self):
        self.client.force_login(self.staff)
        article = Article.objects.create(
            title="Measured dashboard article",
            slug="measured-dashboard-article",
            content="<p>Measured</p>",
            author=self.staff,
            category=self.category,
            status=ArticleStatus.DRAFT,
        )
        for surface, url in (
            ("dashboard_article_catalog", reverse("dashboard:blog:catalog")),
            ("dashboard_article_edit", reverse("dashboard:blog:edit", args=[article.pk])),
        ):
            measure(self, surface, url)
        owner = get_user_model().objects.create_superuser("matrix-content-owner", "matrix@example.test", "secret")
        self.client.force_login(owner)
        measure(self, "dashboard_help_catalog", reverse("dashboard:support:articles"))


class SupportMatrixTests(CustomerTicketDiscoveryTests):
    def test_measure_support_scaling(self):
        self.assertEqual(connection.vendor, "postgresql")
        customer_small = measure(self, "customer_ticket_list_small", reverse("support:ticket_list"))
        for index in range(20):
            from support.services import create_ticket
            create_ticket(user=self.user, category=self.category, subject=f"Matrix {index}", message="Fixture")
        customer_full = measure(self, "customer_ticket_list_full", reverse("support:ticket_list"))
        few = measure(self, "customer_ticket_detail_few", reverse("support:ticket_detail", args=[self.waiting_customer.pk]))
        self.client.force_login(self.staff)
        dashboard_few = measure(self, "dashboard_support_detail_few", reverse("dashboard:support:detail", args=[self.waiting_customer.pk]))
        self.client.force_login(self.user)
        for index in range(12):
            TicketReply.objects.create(
                ticket=self.waiting_customer,
                author=self.staff if index % 2 else self.user,
                message=f"Measured reply {index}",
                is_staff_reply=bool(index % 2),
            )
        many = measure(self, "customer_ticket_detail_many", reverse("support:ticket_detail", args=[self.waiting_customer.pk]))
        self.client.force_login(self.staff)
        measure(self, "dashboard_support_list", reverse("dashboard:support:inbox"))
        dashboard_many = measure(self, "dashboard_support_detail_many", reverse("dashboard:support:detail", args=[self.waiting_customer.pk]))
        self.assertEqual(few.query_count, many.query_count)
        self.assertEqual(dashboard_few.query_count, dashboard_many.query_count)
        self.assertFalse(
            any("FROM \"auth_user\" WHERE \"auth_user\".\"id\"" in shape for shape, _count in dashboard_many.duplicate_shapes),
            "dashboard reply authors must be joined with the reply preparation query",
        )
        self.assertFalse(
            any("support_ticketattachment" in shape for shape, _count in many.duplicate_shapes),
            "customer ticket detail must prepare reply attachments only once",
        )
        print(f"PERFORMANCE_SCALING surface=support_list small={customer_small.query_count} full={customer_full.query_count}")
        print(f"PERFORMANCE_SCALING surface=support_detail small={few.query_count} full={many.query_count}")
        print(f"PERFORMANCE_SCALING surface=dashboard_support_detail small={dashboard_few.query_count} full={dashboard_many.query_count}")


# These diagnostic cases reuse mature fixture builders from regression tests,
# but the performance suite must execute only the measurement methods declared
# in this module.  Shadow inherited ``test_*`` methods so Django's unittest
# loader does not rerun unrelated behavioral/budget assertions here.
for _matrix_case in tuple(globals().values()):
    if (
        isinstance(_matrix_case, type)
        and issubclass(_matrix_case, TestCase)
        and _matrix_case.__module__ == __name__
        and _matrix_case.__name__.endswith("MatrixTests")
    ):
        for _test_name in dir(_matrix_case):
            if _test_name.startswith("test_") and _test_name not in _matrix_case.__dict__:
                setattr(_matrix_case, _test_name, None)


def load_tests(loader, tests, pattern):
    """Keep imported fixture cases out of this diagnostic module's suite."""

    suite = unittest.TestSuite()
    for matrix_case in tuple(globals().values()):
        if (
            isinstance(matrix_case, type)
            and issubclass(matrix_case, TestCase)
            and matrix_case.__module__ == __name__
            and matrix_case.__name__.endswith("MatrixTests")
        ):
            for test_name in sorted(matrix_case.__dict__):
                if test_name.startswith("test_measure"):
                    suite.addTest(matrix_case(test_name))
    return suite
