from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils.translation import override

from support.forms import TicketCreateForm
from support.launch_help_manifest import LAUNCH_CATEGORIES, LAUNCH_HELP_ARTICLES
from support.models import HelpArticle, Ticket, TicketCategory
from support.taxonomy import REFUND_CATEGORY_SLUG


class LaunchHelpSeedCommandTests(TestCase):
    def test_dry_run_makes_no_persistent_mutations(self):
        output = StringIO()
        call_command("seed_launch_help", stdout=output)
        self.assertEqual(TicketCategory.objects.count(), 0)
        self.assertEqual(HelpArticle.objects.count(), 0)
        self.assertIn("Dry run complete", output.getvalue())

    def test_apply_creates_exact_taxonomy_and_complete_article_pack_idempotently(self):
        call_command("seed_launch_help", "--apply")

        self.assertEqual(set(TicketCategory.objects.values_list("slug", flat=True)), {item.slug for item in LAUNCH_CATEGORIES})
        self.assertEqual(HelpArticle.objects.count(), 24)
        self.assertEqual(HelpArticle.objects.filter(is_published=True).count(), 24)
        self.assertEqual(HelpArticle.objects.filter(is_featured=True).count(), 6)
        for category in LAUNCH_CATEGORIES:
            self.assertEqual(HelpArticle.objects.filter(category__slug=category.slug).count(), 4)
        for article in LAUNCH_HELP_ARTICLES:
            saved = HelpArticle.objects.get(slug=article.slug)
            self.assertTrue(all((saved.title, saved.title_fa, saved.summary, saved.summary_fa, saved.content, saved.content_fa)))
            self.assertEqual(saved.category.slug, article.category_slug)

        call_command("seed_launch_help", "--apply")
        self.assertEqual(TicketCategory.objects.count(), 6)
        self.assertEqual(HelpArticle.objects.count(), 24)

    def test_normalizes_legacy_refund_category_in_place_and_preserves_ticket_fk(self):
        user = get_user_model().objects.create_user("refund-ticket-owner", password="secret")
        legacy = TicketCategory.objects.create(name="Refund", name_fa="بازپرداخت", slug="refund")
        ticket = Ticket.objects.create(user=user, category=legacy, subject="Refund question", message="Please help")

        call_command("seed_launch_help", "--apply")

        legacy.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(legacy.slug, REFUND_CATEGORY_SLUG)
        self.assertEqual(legacy.name, "Returns & Refunds")
        self.assertEqual(ticket.category_id, legacy.id)
        self.assertEqual(TicketCategory.objects.filter(slug__in=("refund", REFUND_CATEGORY_SLUG)).count(), 1)

    def test_divergent_operator_article_is_not_overwritten(self):
        category = TicketCategory.objects.create(name="Support", slug="support")
        article = HelpArticle.objects.create(
            category=category, slug="creating-a-support-ticket", title="Operator guide", content="Operator content"
        )
        call_command("seed_launch_help", "--apply")
        article.refresh_from_db()
        self.assertEqual(article.title, "Operator guide")
        self.assertEqual(article.content, "Operator content")

    def test_exact_manifest_article_repairs_missing_publication_timestamp(self):
        call_command("seed_launch_help", "--apply")
        article = HelpArticle.objects.get(slug="how-to-place-an-order")
        article.published_at = None
        article.save(update_fields=("published_at",))

        call_command("seed_launch_help", "--apply")

        article.refresh_from_db()
        self.assertIsNotNone(article.published_at)

    @override_settings(APP_ENV="production")
    def test_production_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command("seed_launch_help", "--apply")

    @override_settings(APP_ENV="local_production")
    def test_local_production_requires_postgresql(self):
        with self.assertRaises(CommandError):
            call_command("seed_launch_help", "--apply")


class LaunchHelpPublicSurfaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_launch_help", "--apply")

    def test_ticket_form_has_only_the_six_canonical_categories(self):
        user = get_user_model().objects.create_user("support-form-owner", password="secret")
        form = TicketCreateForm(user=user)
        self.assertEqual(set(form.fields["category"].queryset.values_list("slug", flat=True)), {item.slug for item in LAUNCH_CATEGORIES})
        self.assertFalse(TicketCategory.objects.filter(slug="refund").exists())
        with override("fa"):
            labels = [form.fields["category"].label_from_instance(category) for category in form.fields["category"].queryset]
        self.assertIn("مرجوعی و بازپرداخت", labels)

    def test_english_and_persian_help_surfaces_render_manifest_content(self):
        english_home = self.client.get("/en/support/")
        persian_home = self.client.get("/fa/support/")
        listing = self.client.get("/en/support/help/", {"topic": "refunds", "q": "refund"})
        detail = self.client.get("/fa/support/help/submitting-a-refund-request/")

        self.assertContains(english_home, "How to place an order")
        self.assertContains(persian_home, "چگونه سفارش ثبت کنم؟")
        self.assertContains(listing, "How to submit a refund request")
        self.assertContains(detail, "چگونه درخواست بازپرداخت ثبت کنم؟")
        self.assertContains(detail, "راهنماهای مرتبط")
        self.assertNotContains(detail, ">How to submit a refund request<")

    def test_category_filter_search_and_related_guides_work(self):
        listing = self.client.get("/en/support/help/", {"category": "payments", "q": "payment"})
        detail = self.client.get("/en/support/help/online-payment-and-confirmation/")

        self.assertContains(listing, "Online payment and payment confirmation")
        self.assertNotContains(listing, "How to place an order")
        self.assertContains(detail, "What to do when an online payment fails")

    def test_help_filter_has_an_htmx_fragment_contract(self):
        response = self.client.get("/en/support/help/", {"topic": "orders"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "How to place an order")
        self.assertNotContains(response, "<html", html=False)
