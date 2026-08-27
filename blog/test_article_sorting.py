from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone, translation

from blog.models import Article, ArticleStatus, BlogCategory, BlogTag


class PublicArticleSortingTests(TestCase):
    def setUp(self):
        self.category = BlogCategory.objects.create(name="Guides", name_fa="راهنما", slug="guides")
        self.other_category = BlogCategory.objects.create(name="Care", name_fa="نگهداری", slug="care")
        self.tag = BlogTag.objects.create(name="Planning", name_fa="برنامه‌ریزی", slug="planning")
        self.articles = []
        titles = (
            ("Zulu", "پ"), ("Alpha", "الف"), ("Beta", "ب"),
            ("Delta", "ت"), ("Echo", "ث"), ("Foxtrot", "ج"),
            ("Golf", "چ"), ("Hotel", "ح"), ("India", "خ"),
            ("Juliet", "د"), ("Kilo", "ذ"), ("Lima", "ر"),
        )
        now = timezone.now()
        for index, (title, title_fa) in enumerate(titles):
            article = Article.objects.create(
                title=title,
                title_fa=title_fa,
                slug=f"sort-{index}",
                summary=f"Searchable planning {index}",
                summary_fa=f"جستجو {index}",
                content="<p>Body</p>",
                content_fa="<p>متن</p>",
                category=self.category if index < 10 else self.other_category,
                status=ArticleStatus.PUBLISHED,
                published_at=now - timedelta(days=index),
            )
            if index % 2 == 0:
                article.tags.add(self.tag)
            self.articles.append(article)
        self.draft = Article.objects.create(
            title="A Draft", title_fa="آ پیش‌نویس", slug="sort-draft",
            content="<p>Draft</p>", content_fa="<p>پیش‌نویس</p>",
            status=ArticleStatus.DRAFT,
        )

    def slugs(self, response):
        return [article.slug for article in response.context["page_obj"]]

    def test_default_newest_and_oldest_are_deterministic(self):
        newest = self.client.get(reverse("blog:article_list"))
        self.assertEqual(newest.context["selected_sort"], "newest")
        self.assertEqual(self.slugs(newest), [article.slug for article in self.articles[:9]])
        oldest = self.client.get(reverse("blog:article_list"), {"sort": "oldest"})
        self.assertEqual(self.slugs(oldest), [article.slug for article in reversed(self.articles[-9:])])

        tied = self.articles[1]
        tied.published_at = self.articles[0].published_at
        tied.save(update_fields=("published_at",))
        tied_response = self.client.get(reverse("blog:article_list"), {"sort": "newest"})
        tied_ids = [article.pk for article in tied_response.context["page_obj"][:2]]
        self.assertEqual(tied_ids, sorted(tied_ids, reverse=True))

    def test_english_and_persian_title_ordering_use_localized_fields(self):
        ascending = self.client.get(reverse("blog:article_list"), {"sort": "title-asc"})
        self.assertEqual(
            [article.title for article in ascending.context["page_obj"]],
            sorted(article.title for article in self.articles)[:9],
        )
        descending = self.client.get(reverse("blog:article_list"), {"sort": "title-desc"})
        self.assertEqual(
            [article.title for article in descending.context["page_obj"]],
            sorted((article.title for article in self.articles), reverse=True)[:9],
        )
        with translation.override("fa"):
            fa_ascending = self.client.get("/fa/articles/", {"sort": "title-asc"})
            fa_descending = self.client.get("/fa/articles/", {"sort": "title-desc"})
        self.assertEqual(
            [article.title_fa for article in fa_ascending.context["page_obj"]],
            sorted(article.title_fa for article in self.articles)[:9],
        )
        self.assertEqual(
            [article.title_fa for article in fa_descending.context["page_obj"]],
            sorted((article.title_fa for article in self.articles), reverse=True)[:9],
        )

    def test_invalid_sort_falls_back_and_drafts_never_appear(self):
        for sort in ("garbage", "newest", "oldest", "title-asc", "title-desc"):
            response = self.client.get(reverse("blog:article_list"), {"sort": sort})
            self.assertNotIn(self.draft.pk, [article.pk for article in response.context["page_obj"]])
        invalid = self.client.get(reverse("blog:article_list"), {"sort": "garbage"})
        self.assertEqual(invalid.context["selected_sort"], "newest")
        self.assertEqual(self.slugs(invalid), [article.slug for article in self.articles[:9]])
        self.assertNotIn("garbage", invalid.context["query_string"])

    def test_sort_composes_with_search_category_and_tag(self):
        params = {
            "q": "Searchable",
            "category": self.category.slug,
            "tag": self.tag.slug,
            "sort": "oldest",
        }
        response = self.client.get(reverse("blog:article_list"), params)
        expected = [article.slug for article in reversed(self.articles[:10]) if article.pk in {
            tagged.pk for tagged in self.articles if tagged.pk % 2 == self.articles[0].pk % 2
        }]
        self.assertEqual(self.slugs(response), expected)
        self.assertContains(response, 'name="q" value="Searchable"', html=False)
        self.assertContains(response, 'name="category" value="guides"', html=False)
        self.assertContains(response, 'name="tag" value="planning"', html=False)

    def test_page_two_preserves_sort_and_sort_form_resets_page(self):
        page_two = self.client.get(reverse("blog:article_list"), {"sort": "oldest", "page": 2})
        self.assertEqual(page_two.context["page_obj"].number, 2)
        self.assertEqual(len(page_two.context["page_obj"]), 3)
        self.assertContains(page_two, "sort=oldest")
        sort_form = page_two.content.decode().split('data-article-sort-form', 1)[1].split("</form>", 1)[0]
        self.assertNotIn('name="page"', sort_form)

    def test_htmx_and_direct_navigation_share_the_article_workspace(self):
        direct = self.client.get(reverse("blog:article_list"), {"sort": "title-asc"})
        self.assertContains(direct, '<section id="article-discovery"', html=False)
        fragment = self.client.get(
            reverse("blog:article_list"),
            {"sort": "title-desc"},
            HTTP_HX_REQUEST="true",
            HTTP_HX_TARGET="article-discovery",
        )
        self.assertContains(fragment, '<section id="article-discovery"', html=False)
        self.assertNotContains(fragment, "<html", html=False)
        self.assertContains(fragment, 'hx-push-url="true"', html=False)
