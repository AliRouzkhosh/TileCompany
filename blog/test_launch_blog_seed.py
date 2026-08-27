from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import translation
from django.utils.html import strip_tags

from blog.models import Article, ArticleStatus, BlogCategory, BlogTag, Comment


class LaunchBlogSeedTests(TestCase):
    def test_dry_run_apply_and_public_contract(self):
        call_command("seed_launch_blog")
        self.assertEqual(Article.objects.count(), 0)
        call_command("seed_launch_blog", "--apply")
        self.assertEqual(BlogCategory.objects.count(), 4)
        self.assertEqual(BlogTag.objects.count(), 12)
        self.assertEqual(Article.objects.count(), 12)
        self.assertEqual(Article.objects.filter(status=ArticleStatus.PUBLISHED, published_at__isnull=False).count(), 12)
        self.assertEqual(Comment.objects.count(), 0)
        for article in Article.objects.all():
            self.assertTrue(all((article.title, article.title_fa, article.summary, article.summary_fa, article.content, article.content_fa, article.meta_title, article.meta_title_fa, article.meta_description, article.meta_description_fa)))
            self.assertGreaterEqual(article.tags.count(), 2)
            self.assertGreaterEqual(len(strip_tags(article.content).split()), 250)
            self.assertGreaterEqual(len(strip_tags(article.content_fa)), 1200)
            self.assertGreaterEqual(article.content.count("<h2>"), 4)
            self.assertNotIn("<script", article.content.lower())
        self.assertContains(self.client.get("/en/articles/", {"page": 2}), "Page 2 of 2")
        with translation.override("fa"):
            self.assertContains(self.client.get("/fa/articles/", {"q": "بندکشی"}), "راهنمای بندکشی")
            self.assertContains(self.client.get("/fa/articles/tile-adhesive-basics/"), "آشنایی با چسب کاشی")
        self.assertContains(self.client.get("/en/articles/", {"tag": "adhesives"}), "Tile Adhesive Basics")
        call_command("seed_launch_blog", "--apply")
        self.assertEqual(Article.objects.count(), 12)

    @override_settings(APP_ENV="production")
    def test_production_is_rejected(self):
        with self.assertRaises(CommandError): call_command("seed_launch_blog", "--apply")

    @override_settings(APP_ENV="local_production")
    def test_local_production_requires_postgresql(self):
        with self.assertRaises(CommandError): call_command("seed_launch_blog", "--apply")
