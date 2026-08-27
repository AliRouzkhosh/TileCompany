from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from blog.models import Article, ArticleStatus
from products.catalog_presentation import prepare_product_card_presentations
from products.models import Product, ProductType

from .queries import capture_route


class PostgreSQLPerformanceSmokeTests(TestCase):
    """Deterministic route smoke measurements; test runner owns all fixtures."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="performance-customer",
            password="not-used-by-the-test-client",
        )
        cls.article = Article.objects.create(
            title="Performance fixture article",
            slug="performance-fixture-article",
            author=cls.user,
            content="<p>Deterministic performance fixture.</p>",
            status=ArticleStatus.PUBLISHED,
            published_at=timezone.now(),
        )

    def setUp(self):
        self.assertEqual(connection.vendor, "postgresql")

    def test_initial_route_measurements(self):
        routes = (
            ("homepage", lambda: self.client.get(reverse("products:home"))),
            ("catalog", lambda: self.client.get(reverse("products:product_list"))),
            ("article_index", lambda: self.client.get(reverse("blog:article_list"))),
            ("article_detail", lambda: self.client.get(self.article.get_absolute_url())),
        )
        for surface, request in routes:
            response, _measurement = capture_route(surface, request)
            self.assertEqual(response.status_code, 200)

        self.client.force_login(self.user)
        response, _measurement = capture_route(
            "account_profile",
            lambda: self.client.get(reverse("accounts:profile")),
        )
        self.assertEqual(response.status_code, 200)

    def test_prepared_product_cards_are_bounded(self):
        product_type = ProductType.objects.create(code="perf-tile", name="Performance Tile")
        products = [
            Product.objects.create(
                product_type=product_type,
                name=f"Performance product {number}",
                slug=f"performance-product-{number}",
                sku=f"PERF-{number}",
            )
            for number in range(1, 5)
        ]
        _one, one = capture_route(
            "prepared_product_cards_1",
            lambda: _PresentationResponse(prepare_product_card_presentations(products[:1], language="en")),
        )
        _four, four = capture_route(
            "prepared_product_cards_4",
            lambda: _PresentationResponse(prepare_product_card_presentations(products, language="en")),
        )
        self.assertEqual(one.query_count, four.query_count)


class _PresentationResponse:
    """Minimal response contract for exercising a non-route established path."""

    status_code = 200

    def __init__(self, _result):
        pass
