from datetime import timedelta
from importlib import import_module

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from blog.models import SitePage, SitePageKey


EXPECTED_KEYS = {
    "about",
    "privacy",
    "terms",
    "shipping-delivery",
    "returns-refunds",
    "payment-information",
}


def complete_page(page):
    page.title = "Policy title"
    page.title_fa = "عنوان سیاست"
    page.content = "<h2>Policy section</h2><p>Approved content.</p>"
    page.content_fa = "<h2>بخش سیاست</h2><p>محتوای تأییدشده.</p>"
    return page


class SitePageCanonicalIdentityTests(TestCase):
    def test_migration_creates_exactly_six_empty_unpublished_identities(self):
        self.assertEqual(set(SitePage.objects.values_list("key", flat=True)), EXPECTED_KEYS)
        self.assertEqual(set(SitePageKey.values), EXPECTED_KEYS)
        for page in SitePage.objects.all():
            self.assertFalse(page.is_published)
            self.assertIsNone(page.published_at)
            self.assertEqual(page.title, "")
            self.assertEqual(page.title_fa, "")
            self.assertEqual(page.content, "")
            self.assertEqual(page.content_fa, "")
            self.assertEqual(page.summary, "")
            self.assertEqual(page.summary_fa, "")
            self.assertEqual(page.meta_title, "")
            self.assertEqual(page.meta_title_fa, "")
            self.assertEqual(page.meta_description, "")
            self.assertEqual(page.meta_description_fa, "")

    def test_empty_canonical_draft_saves(self):
        page = SitePage.objects.get(key=SitePageKey.PRIVACY)
        page.save()
        self.assertFalse(page.is_published)

    def test_key_is_unique_and_database_restricts_arbitrary_values(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SitePage.objects.create(key=SitePageKey.ABOUT)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SitePage.objects.bulk_create([SitePage(key="arbitrary")])

    def test_arbitrary_key_is_rejected_by_model_validation(self):
        with self.assertRaises(ValidationError) as raised:
            SitePage(key="arbitrary").save()
        self.assertIn("key", raised.exception.message_dict)

    def test_persisted_key_is_immutable_and_original_row_survives(self):
        page = SitePage.objects.get(key=SitePageKey.PRIVACY)
        original_pk = page.pk
        page.key = SitePageKey.TERMS
        with self.assertRaises(ValidationError) as raised:
            page.save()
        self.assertIn("cannot be changed", raised.exception.message_dict["key"][0])
        self.assertEqual(SitePage.objects.get(pk=original_pk).key, SitePageKey.PRIVACY)

    def test_canonical_migration_is_non_destructive_when_reapplied(self):
        page = SitePage.objects.get(key=SitePageKey.PRIVACY)
        page.title = "Operator-authored draft"
        page.save()
        migration = import_module("blog.migrations.0008_seed_canonical_site_pages")
        migration.create_canonical_site_pages(apps, None)
        page.refresh_from_db()
        self.assertEqual(page.title, "Operator-authored draft")
        self.assertEqual(SitePage.objects.count(), 6)


class SitePagePublicationTests(TestCase):
    def test_publication_requires_bilingual_titles_and_content(self):
        scenarios = {
            "empty": {},
            "english only": {"title": "English", "content": "<p>English</p>"},
            "persian only": {"title_fa": "فارسی", "content_fa": "<p>فارسی</p>"},
            "missing english title": {
                "title_fa": "فارسی", "content": "<p>English</p>", "content_fa": "<p>فارسی</p>",
            },
            "missing persian content": {
                "title": "English", "title_fa": "فارسی", "content": "<p>English</p>",
            },
        }
        for label, values in scenarios.items():
            with self.subTest(label=label):
                page = SitePage.objects.get(key=SitePageKey.TERMS)
                for field in ("title", "title_fa", "content", "content_fa"):
                    setattr(page, field, values.get(field, ""))
                page.is_published = True
                with self.assertRaises(ValidationError):
                    page.save()

    def test_complete_bilingual_page_can_publish(self):
        page = complete_page(SitePage.objects.get(key=SitePageKey.TERMS))
        page.is_published = True
        page.save()
        self.assertIsNotNone(page.published_at)

    def test_published_at_is_first_publication_time_and_never_resets(self):
        page = complete_page(SitePage.objects.get(key=SitePageKey.PAYMENT_INFORMATION))
        page.is_published = True
        page.save()
        first_published_at = page.published_at

        prior_updated_at = timezone.now() - timedelta(days=1)
        SitePage.objects.filter(pk=page.pk).update(updated_at=prior_updated_at)
        page.refresh_from_db()
        page.summary = "A later edit"
        page.save()
        page.refresh_from_db()
        self.assertEqual(page.published_at, first_published_at)
        self.assertGreater(page.updated_at, prior_updated_at)

        page.is_published = False
        page.save()
        page.refresh_from_db()
        self.assertEqual(page.published_at, first_published_at)

        page.is_published = True
        page.save()
        page.refresh_from_db()
        self.assertEqual(page.published_at, first_published_at)

    def test_partial_first_publish_persists_published_at(self):
        page = complete_page(SitePage.objects.get(key=SitePageKey.ABOUT))
        page.save()
        page.is_published = True
        page.save(update_fields=["is_published"])
        page.refresh_from_db()
        self.assertTrue(page.is_published)
        self.assertIsNotNone(page.published_at)


class SitePageSanitizationTests(TestCase):
    def test_same_article_sanitizer_contract_applies_to_both_languages(self):
        unsafe = (
            '<h2 onclick="attack()">Heading</h2>'
            '<script>alert(1)</script>'
            '<p>Paragraph <strong>strong</strong></p>'
            '<ul><li>Item</li></ul>'
            '<a href="javascript:attack()" onmouseover="attack()">Bad link</a>'
            '<a href="https://example.com/help">Safe link</a>'
        )
        page = SitePage.objects.get(key=SitePageKey.SHIPPING_DELIVERY)
        page.content = unsafe
        page.content_fa = unsafe
        page.save()
        page.refresh_from_db()

        for content in (page.content, page.content_fa):
            self.assertNotIn("<script", content)
            self.assertNotIn("alert(1)", content)
            self.assertNotIn("onclick", content)
            self.assertNotIn("onmouseover", content)
            self.assertNotIn("javascript:", content)
            self.assertIn("<h2>Heading</h2>", content)
            self.assertIn("<strong>strong</strong>", content)
            self.assertIn("<ul><li>Item</li></ul>", content)
            self.assertIn('href="https://example.com/help"', content)
