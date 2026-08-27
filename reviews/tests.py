from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import TestCase
from django.urls import reverse

from products.currency import Currency
from products.models import Product, ProductVariant, VariantPrice
from products.selectors import get_filtered_products, get_product_by_slug, get_product_review_queryset, get_sorted_products
from reviews.models import Review, ReviewComment


class ReviewModerationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("reviewer", password="secret")
        self.other_user = get_user_model().objects.create_user("other", password="secret")
        self.product = Product.objects.create(name="Review Tile", slug="review-tile", sku="REVIEW-TILE")
        self.review = Review.objects.create(product=self.product, user=self.user, rating=4, comment="Good tile", is_approved=True)

    def test_anonymous_review_selector_does_not_filter_by_anonymous_user(self):
        self.assertEqual(list(get_product_review_queryset(self.product, AnonymousUser())), [self.review])

    def test_anonymous_visitor_can_open_product_page(self):
        response = self.client.get(reverse("products:product_detail", args=[self.product.slug]))
        self.assertEqual(response.status_code, 200)

    def test_review_html_payload_is_rendered_as_plain_text(self):
        self.review.comment = '<img src=x onerror="alert(1)"><script>alert(1)</script>'
        self.review.save(update_fields=["comment"])
        response = self.client.get(reverse("products:product_detail", args=[self.product.slug]))
        self.assertContains(response, "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", html=False)
        self.assertNotContains(response, '<img src=x onerror="alert(1)">', html=False)

    def test_editing_approved_review_returns_it_to_moderation(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("products:edit_review", args=[self.review.pk]), {"rating": 5, "comment": "Changed review"})
        self.assertRedirects(response, f"{self.product.get_absolute_url()}#reviews", fetch_redirect_response=False)
        self.review.refresh_from_db()
        self.assertFalse(self.review.is_approved)

    def test_pending_review_cannot_receive_comments(self):
        self.review.is_approved = False
        self.review.save(update_fields=["is_approved"])
        self.client.force_login(self.other_user)
        response = self.client.post(reverse("products:add_comment", args=[self.review.pk]), {"comment": "Hidden thread"})
        self.assertEqual(response.status_code, 404)

    def test_editing_approved_comment_returns_it_to_moderation(self):
        comment = ReviewComment.objects.create(review=self.review, user=self.user, comment="Useful", is_approved=True)
        self.client.force_login(self.user)
        response = self.client.post(reverse("products:edit_comment", args=[comment.pk]), {"comment": "Changed comment"})
        self.assertRedirects(response, f"{self.product.get_absolute_url()}#reviews", fetch_redirect_response=False)
        comment.refresh_from_db()
        self.assertFalse(comment.is_approved)

    def test_whitespace_comment_is_rejected(self):
        self.client.force_login(self.other_user)
        response = self.client.post(reverse("products:add_comment", args=[self.review.pk]), {"comment": "   "})
        self.assertRedirects(response, f"{self.product.get_absolute_url()}#reviews", fetch_redirect_response=False)

    def test_htmx_review_edit_returns_only_reviews_workspace(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("products:edit_review", args=[self.review.pk]),
            {"rating": 5, "comment": "Changed review"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="product-reviews-region"')
        self.assertNotContains(response, "<html", html=False)
        self.review.refresh_from_db()
        self.assertFalse(self.review.is_approved)

    def test_htmx_invalid_reply_returns_reviews_workspace_without_mutation(self):
        self.client.force_login(self.other_user)
        response = self.client.post(
            reverse("products:add_comment", args=[self.review.pk]),
            {"comment": "   "},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="product-reviews-region"')
        self.assertFalse(ReviewComment.objects.exists())

    def test_discussion_count_matches_visible_comments_for_each_viewer(self):
        for index in range(3):
            ReviewComment.objects.create(
                review=self.review,
                user=self.other_user,
                comment=f"Approved {index}",
                is_approved=True,
            )
        pending = ReviewComment.objects.create(
            review=self.review,
            user=self.user,
            comment="Pending mine",
            is_approved=False,
        )

        owner_review = get_product_review_queryset(self.product, self.user).get()
        other_review = get_product_review_queryset(self.product, self.other_user).get()

        self.assertEqual(owner_review.reply_count, 4)
        self.assertEqual(other_review.reply_count, 3)
        self.assertIn(pending, list(owner_review.comments.all()))
        self.assertNotIn(pending, list(other_review.comments.all()))


class ProductRatingSummaryTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name="Rated Tile", slug="rated-tile", sku="RATED-TILE")
        self.variant_one = ProductVariant.objects.create(product=self.product, sku="RATED-TILE-1", stock=10)
        self.variant_two = ProductVariant.objects.create(product=self.product, sku="RATED-TILE-2", stock=20)
        VariantPrice.objects.create(variant=self.variant_one, currency=Currency.USD, amount="10.00")
        VariantPrice.objects.create(variant=self.variant_two, currency=Currency.USD, amount="20.00")
        self.users = [
            get_user_model().objects.create_user(f"rating-user-{index}", password="secret")
            for index in range(1, 5)
        ]

    def _review(self, user_index, rating, *, approved=True):
        return Review.objects.create(
            product=self.product,
            user=self.users[user_index],
            rating=rating,
            comment=f"Review {user_index}",
            is_approved=approved,
        )

    def test_public_summary_uses_approved_reviews_only_without_multiplying_stock(self):
        approved_five = self._review(0, 5)
        self._review(1, 4)
        self._review(2, 1, approved=False)
        ReviewComment.objects.create(review=approved_five, user=self.users[3], comment="A comment", is_approved=True)

        product, _ = get_filtered_products(currency=Currency.USD)
        product = product.get(pk=self.product.pk)

        self.assertEqual(product.avg_rating, 4.5)
        self.assertEqual(product.review_count, 2)
        self.assertEqual(product.total_variant_stock, 30)
        self.assertEqual(product.requested_currency_min_price, 10)
        self.assertEqual(product.requested_currency_max_price, 20)

    def test_pending_only_reviews_are_absent_and_detail_has_empty_state(self):
        self._review(0, 1, approved=False)

        product = get_product_by_slug(self.product.slug, Currency.USD)
        self.assertIsNone(product.avg_rating)
        self.assertEqual(product.review_count, 0)

        english = self.client.get(f"/en/product/{self.product.slug}/")
        persian = self.client.get(f"/fa/product/{self.product.slug}/")
        self.assertContains(english, "No reviews yet")
        self.assertContains(persian, "هنوز دیدگاهی ثبت نشده است")
        self.assertNotContains(english, "★")

    def test_one_approved_review_has_a_single_public_rating(self):
        self._review(0, 5)
        self.product.is_featured = True
        self.product.save(update_fields=["is_featured"])

        product = get_product_by_slug(self.product.slug, Currency.USD)

        self.assertEqual(product.avg_rating, 5.0)
        self.assertEqual(product.review_count, 1)

        catalog = self.client.get("/en/shop/")
        detail = self.client.get(f"/en/product/{self.product.slug}/")
        persian_detail = self.client.get(f"/fa/product/{self.product.slug}/")
        self.assertContains(catalog, "data-rating-icon")
        self.assertContains(catalog, "5 (1)")
        self.assertContains(detail, "5 / 5")
        self.assertContains(detail, "1 review")
        self.assertContains(persian_detail, "امتیاز ۵ از ۵ بر پایهٔ ۱ دیدگاه")

    def test_rating_sort_uses_annotated_approved_summary(self):
        lower = Product.objects.create(name="Lower Rated", slug="lower-rated", sku="LOWER-RATED")
        lower_variant = ProductVariant.objects.create(product=lower, sku="LOWER-RATED-1")
        VariantPrice.objects.create(variant=lower_variant, currency=Currency.USD, amount="12.00")
        Review.objects.create(product=lower, user=self.users[0], rating=3, comment="Lower", is_approved=True)
        self._review(1, 5)

        products, _ = get_filtered_products(currency=Currency.USD)
        sorted_products = list(get_sorted_products(products, "rating"))

        self.assertEqual(sorted_products[0], self.product)

    def test_catalog_rating_annotations_do_not_add_queries_per_product(self):
        with CaptureQueriesContext(connection) as one_product_queries:
            list(get_filtered_products(currency=Currency.USD)[0])

        second = Product.objects.create(name="Second Tile", slug="second-tile", sku="SECOND-TILE")
        second_variant = ProductVariant.objects.create(product=second, sku="SECOND-TILE-1")
        VariantPrice.objects.create(variant=second_variant, currency=Currency.USD, amount="15.00")
        self._review(0, 4)

        with CaptureQueriesContext(connection) as two_product_queries:
            list(get_filtered_products(currency=Currency.USD)[0])

        self.assertEqual(len(one_product_queries), len(two_product_queries))
