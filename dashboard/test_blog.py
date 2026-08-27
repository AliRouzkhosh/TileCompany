from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone, translation

from blog.models import Article, ArticleStatus, BlogCategory, BlogTag
from dashboard.forms_blog import ArticleForm
from dashboard.management.commands.seed_dashboard_roles import Command as SeedDashboardRolesCommand
from dashboard.selectors.blog import category_queryset, tag_queryset
from products.models import Product


class BlogDashboardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.staff = User.objects.create_user("editor", password="secret", is_staff=True)
        SeedDashboardRolesCommand().handle()
        self.staff.groups.add(Group.objects.get(name="Content Manager"))
        self.customer = User.objects.create_user("customer", password="secret")
        self.category = BlogCategory.objects.create(name="Guides", slug="guides")

    def article(self, **kwargs):
        values = {"title": "Tile guide", "slug": "tile-guide", "content": "<p>Content</p>", "author": self.staff, "category": self.category}
        values.update(kwargs)
        return Article.objects.create(**values)

    def article_post_data(self, **kwargs):
        values = {
            "title": "Persistence article", "title_fa": "مقالهٔ پایدار",
            "slug": "persistence-article", "summary": "Summary", "summary_fa": "خلاصه",
            "content": "<p>Content</p>", "content_fa": "<p>محتوا</p>",
            "status": ArticleStatus.DRAFT, "published_at": "", "author": self.staff.pk,
            "category": self.category.pk, "tags": [], "related_products": [],
            "meta_title": "", "meta_title_fa": "", "meta_description": "", "meta_description_fa": "",
            "attachments-TOTAL_FORMS": "0", "attachments-INITIAL_FORMS": "0",
            "attachments-MIN_NUM_FORMS": "0", "attachments-MAX_NUM_FORMS": "1000",
        }
        values.update(kwargs)
        return values

    def test_scheduled_article_is_not_public_but_staff_can_preview(self):
        article = self.article(status=ArticleStatus.PUBLISHED, published_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.client.get(article.get_absolute_url()).status_code, 404)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("dashboard:blog:preview", args=[article.pk])).status_code, 200)

    def test_dashboard_requires_staff_and_can_create_article(self):
        self.client.force_login(self.customer)
        self.assertNotEqual(self.client.get(reverse("dashboard:blog:catalog")).status_code, 200)
        self.client.force_login(self.staff)
        response = self.client.post(reverse("dashboard:blog:create"), {"title": "New", "slug": "new", "content": "<p>Hello</p>", "status": "draft", "author": self.staff.pk, "category": self.category.pk, "attachments-TOTAL_FORMS": "0", "attachments-INITIAL_FORMS": "0", "attachments-MIN_NUM_FORMS": "0", "attachments-MAX_NUM_FORMS": "1000"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Article.objects.filter(slug="new").exists())

    def test_public_search_and_tag_filter_are_distinct(self):
        tag = BlogTag.objects.create(name="Installation", slug="installation")
        article = self.article(status=ArticleStatus.PUBLISHED, published_at=timezone.now())
        article.tags.add(tag)
        response = self.client.get(reverse("blog:article_list"), {"q": "Tile", "tag": "installation"})
        self.assertContains(response, "Tile guide")

    def test_article_taxonomy_is_first_class_database_backed_and_public(self):
        self.client.force_login(self.staff)
        create_page = self.client.get(reverse("dashboard:blog:create"))
        self.assertContains(create_page, reverse("dashboard:blog:categories"))
        self.assertContains(create_page, reverse("dashboard:blog:tags"))
        self.assertContains(create_page, "Manage categories")
        self.assertContains(create_page, "Manage tags")

        category_response = self.client.post(reverse("dashboard:blog:category_create"), {
            "name": "Architecture", "name_fa": "معماری", "slug": "architecture",
            "description": "", "description_fa": "", "icon": "", "color": "emerald",
        })
        self.assertRedirects(category_response, reverse("dashboard:blog:categories"))
        category = BlogCategory.objects.get(slug="architecture")
        selector_page = self.client.get(reverse("dashboard:blog:create"))
        self.assertContains(selector_page, "Architecture")

        create_article = self.client.post(reverse("dashboard:blog:create"), self.article_post_data(
            slug="architecture-article", title="Architecture article", category=category.pk,
            status=ArticleStatus.PUBLISHED, published_at=timezone.now().strftime("%Y-%m-%dT%H:%M"),
        ))
        self.assertEqual(create_article.status_code, 302)
        article = Article.objects.get(slug="architecture-article")
        self.assertEqual(article.category, category)

        public_list = self.client.get(reverse("blog:article_list"))
        filtered = self.client.get(reverse("blog:article_list"), {"category": category.slug})
        detail = self.client.get(article.get_absolute_url())
        self.assertContains(public_list, "Architecture")
        self.assertContains(filtered, "Architecture article")
        self.assertContains(detail, "Architecture")
        self.assertContains(detail, f"?category={category.slug}")
        self.assertContains(selector_page, self.category.name)

        for index in range(30):
            BlogCategory.objects.create(name=f"Editorial {index}", slug=f"editorial-{index}")
        form = ArticleForm(user=self.staff)
        self.assertEqual(form.fields["category"].queryset.count(), BlogCategory.objects.count())

    def test_category_and_tag_crud_counts_and_linked_delete_guards(self):
        self.client.force_login(self.staff)
        tag = BlogTag.objects.create(name="Materials", name_fa="مصالح", slug="materials")
        article = self.article()
        article.tags.add(tag)

        with CaptureQueriesContext(connection) as category_queries:
            list(category_queryset())
        with CaptureQueriesContext(connection) as tag_queries:
            list(tag_queryset())
        self.assertEqual(len(category_queries), 1)
        self.assertEqual(len(tag_queries), 1)
        self.assertEqual(category_queryset().get(pk=self.category.pk).article_count, 1)
        self.assertEqual(tag_queryset().get(pk=tag.pk).article_count, 1)

        edit_category = self.client.post(reverse("dashboard:blog:category_edit", args=[self.category.pk]), {
            "name": "Guides updated", "name_fa": "راهنماها", "slug": "guides-updated",
            "description": "", "description_fa": "", "icon": "", "color": "emerald",
        })
        self.assertRedirects(edit_category, reverse("dashboard:blog:categories"))
        self.category.refresh_from_db()
        self.assertEqual(self.category.name_fa, "راهنماها")

        edit_tag = self.client.post(reverse("dashboard:blog:tag_edit", args=[tag.pk]), {
            "name": "Materials updated", "name_fa": "مصالح جدید", "slug": "materials-updated",
        })
        self.assertRedirects(edit_tag, reverse("dashboard:blog:tags"))
        tag.refresh_from_db()
        self.assertEqual(tag.name_fa, "مصالح جدید")

        self.client.post(reverse("dashboard:blog:category_delete", args=[self.category.pk]))
        self.client.post(reverse("dashboard:blog:tag_delete", args=[tag.pk]))
        self.assertTrue(BlogCategory.objects.filter(pk=self.category.pk).exists())
        self.assertTrue(BlogTag.objects.filter(pk=tag.pk).exists())

        unused_category = BlogCategory.objects.create(name="Unused", slug="unused")
        unused_tag = BlogTag.objects.create(name="Unused tag", slug="unused-tag")
        self.client.post(reverse("dashboard:blog:category_delete", args=[unused_category.pk]))
        self.client.post(reverse("dashboard:blog:tag_delete", args=[unused_tag.pk]))
        self.assertFalse(BlogCategory.objects.filter(pk=unused_category.pk).exists())
        self.assertFalse(BlogTag.objects.filter(pk=unused_tag.pk).exists())

    def test_actual_ckeditor_output_survives_dashboard_save_reload_public_and_second_save(self):
        self.client.force_login(self.staff)
        actual = (
            '<p style="text-align:center;">Center</p>'
            '<p style="text-align:right;">End</p>'
            '<p style="text-align:justify;">Justify</p>'
            '<figure class="image image_resized article-image-align-end" style="width:50%;">'
            '<img style="aspect-ratio:250/337;" src="/media/a.jpg" width="250" height="337" alt="Alt">'
            '<figcaption>Caption</figcaption></figure>'
        )
        response = self.client.post(reverse("dashboard:blog:create"), self.article_post_data(content=actual))
        self.assertEqual(response.status_code, 302)
        article = Article.objects.get(slug="persistence-article")
        first = article.content
        for expected in (
            "article-align-center", "article-align-end", "article-align-justify",
            "article-image-medium", "article-image-align-end", 'alt="Alt"', "<figcaption>Caption</figcaption>",
        ):
            self.assertIn(expected, first)
        self.assertNotIn("style=", first)

        edit_page = self.client.get(reverse("dashboard:blog:edit", args=[article.pk]))
        self.assertContains(edit_page, "article-align-justify")
        preview = self.client.get(reverse("dashboard:blog:preview", args=[article.pk]))
        article.status = ArticleStatus.PUBLISHED
        article.published_at = timezone.now()
        article.save()
        public = self.client.get(article.get_absolute_url())
        for output in (preview, public):
            self.assertContains(output, "article-align-justify")
            self.assertContains(output, "article-image-medium")
            self.assertContains(output, "Caption")

        second = self.client.post(reverse("dashboard:blog:edit", args=[article.pk]), self.article_post_data(
            content=first, slug=article.slug, title=article.title,
            status=ArticleStatus.PUBLISHED, published_at=article.published_at.strftime("%Y-%m-%dT%H:%M"),
        ))
        self.assertEqual(second.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.content, first)

    def test_fa_and_en_preview_public_parity_and_directional_alignment(self):
        self.client.force_login(self.staff)
        article = self.article(
            slug="parity", status=ArticleStatus.PUBLISHED, published_at=timezone.now(),
            content='<p style="text-align:right;" onclick="x">English end</p><script>bad-en</script>',
            content_fa='<p style="text-align:right;" onload="x">شروع فارسی Mixed</p><script>bad-fa</script>',
        )
        en_preview = self.client.get(reverse("dashboard:blog:preview", args=[article.pk]))
        en_public = self.client.get(article.get_absolute_url())
        with translation.override("fa"):
            fa_preview = self.client.get(f"/fa/dashboard/blog/{article.pk}/preview/")
            fa_public = self.client.get(f"/fa/articles/{article.slug}/")
        for output in (en_preview, en_public):
            self.assertContains(output, "article-align-end")
            self.assertNotContains(output, "bad-en")
        for output in (fa_preview, fa_public):
            self.assertContains(output, "article-align-start")
            self.assertContains(output, "شروع فارسی Mixed")
            self.assertNotContains(output, "bad-fa")

    def test_editor_contract_has_finite_controls_and_excludes_unrestricted_features(self):
        ltr = settings.CKEDITOR_5_CONFIGS["article_ltr"]
        rtl = settings.CKEDITOR_5_CONFIGS["article_rtl"]
        for config in (ltr, rtl):
            toolbar = config["toolbar"]
            for forbidden in ("sourceEditing", "htmlEmbed", "mediaEmbed", "fontFamily", "fontSize", "fontColor", "fontBackgroundColor"):
                self.assertNotIn(forbidden, toolbar)
            self.assertIn("style", config["image"]["toolbar"])
            self.assertFalse(any(control.startswith("resizeImage") for control in config["image"]["toolbar"]))
            image_styles = {
                tuple(definition["classes"])
                for definition in config["style"]["definitions"]
                if definition["element"] == "figure"
            }
            self.assertEqual(
                image_styles,
                {
                    ("article-image-small",),
                    ("article-image-medium",),
                    ("article-image-content",),
                    ("article-image-wide",),
                },
            )
            self.assertEqual(
                [option["className"] for option in config["image"]["styles"]["options"]],
                [
                    "article-image-align-start",
                    "article-image-align-center",
                    "article-image-align-end",
                ],
            )
        self.assertEqual(ltr["alignment"]["options"][0]["className"], "article-align-start")
        self.assertEqual(rtl["alignment"]["options"][0]["name"], "right")

    def test_articles_work_with_zero_or_optional_related_products(self):
        without_product = self.article(slug="without-product", status=ArticleStatus.PUBLISHED, published_at=timezone.now())
        self.assertEqual(without_product.related_products.count(), 0)
        self.assertEqual(self.client.get(without_product.get_absolute_url()).status_code, 200)

        product = Product.objects.create(name="Optional product", slug="optional-product", sku="OPTIONAL-ARTICLE")
        with_product = self.article(slug="with-product", status=ArticleStatus.PUBLISHED, published_at=timezone.now())
        with_product.related_products.add(product)
        response = self.client.get(with_product.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(with_product.related_products.get(), product)
