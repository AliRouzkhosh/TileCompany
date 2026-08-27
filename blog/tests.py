from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone, translation

from blog.models import Article, ArticleStatus, BlogCategory, BlogTag, Comment
from blog.sanitization import normalize_article_editor_html, sanitize_article_html
from blog.services import ARTICLE_DISCUSSION_COOLDOWN_SECONDS


class LanguageIsolationMixin:
    """Restore the translation context changed by localized client requests."""

    def setUp(self):
        super().setUp()
        self.enterContext(translation.override("en"))


class BlogCommentTests(LanguageIsolationMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user("commenter", password="secret")
        self.article = Article.objects.create(title="Article", slug="comment-audit", content="<p>Text</p>", status=ArticleStatus.PUBLISHED, published_at=timezone.now())

    def test_invalid_reply_target_is_not_converted_to_top_level_comment(self):
        self.client.force_login(self.user)
        response = self.client.post(self.article.get_absolute_url(), {"body": "Reply", "reply_to": "999999"})
        self.assertRedirects(response, f"{self.article.get_absolute_url()}#comments")
        self.assertFalse(Comment.objects.exists())

    def test_persian_blog_storefront_prefers_persian_content_and_keeps_english(self):
        category = BlogCategory.objects.create(name="Design Ideas", name_fa="ایده‌های طراحی", slug="design-ideas")
        tag = BlogTag.objects.create(name="Bathroom", name_fa="حمام", slug="bathroom")
        self.article.category = category
        self.article.title_fa = "راهنمای طراحی حمام"
        self.article.summary = "English article summary"
        self.article.summary_fa = "خلاصهٔ فارسی مقاله"
        self.article.content_fa = "<p>محتوای فارسی مقاله</p>"
        self.article.save()
        self.article.tags.add(tag)

        list_response = self.client.get("/fa/articles/")
        detail_response = self.client.get(f"/fa/articles/{self.article.slug}/")
        filtered_response = self.client.get("/fa/articles/?category=design-ideas")

        for response in (list_response, detail_response, filtered_response):
            self.assertEqual(response.status_code, 200)
        self.assertContains(list_response, "مقاله‌ها و الهام‌بخشی")
        self.assertContains(list_response, "راهنمای طراحی حمام")
        self.assertContains(detail_response, "محتوای فارسی مقاله")
        self.assertContains(detail_response, "پیوستن به گفتگو")
        self.assertContains(filtered_response, "ایده‌های طراحی")

        english_response = self.client.get("/en/articles/")
        english_detail_response = self.client.get(f"/en/articles/{self.article.slug}/")
        self.assertContains(english_response, "Articles & Inspiration")
        self.assertContains(english_detail_response, "English article summary")


class CommentEditingTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("owner", password="secret")
        self.other = get_user_model().objects.create_user("other", password="secret")
        self.article = Article.objects.create(
            title="Article", slug="editable-article", content="<p>Text</p>",
            status=ArticleStatus.PUBLISHED, published_at=timezone.now(),
        )

    def _edit_url(self, comment):
        return reverse("blog:comment_edit", args=[comment.pk])

    def test_owner_can_edit_top_level_comment_and_it_returns_to_pending(self):
        comment = Comment.objects.create(article=self.article, user=self.owner, body="Before", is_approved=True)
        self.client.force_login(self.owner)
        response = self.client.post(self._edit_url(comment), {"body": "After"})
        self.assertRedirects(response, f"{self.article.get_absolute_url()}#comment-{comment.pk}")
        comment.refresh_from_db()
        self.assertEqual(comment.body, "After")
        self.assertFalse(comment.is_approved)
        self.assertIsNotNone(comment.edited_at)

    def test_owner_can_edit_reply_without_changing_its_authority(self):
        parent = Comment.objects.create(article=self.article, user=self.other, body="Parent", is_approved=True)
        reply = Comment.objects.create(article=self.article, user=self.owner, parent=parent, body="Before", is_approved=False)
        self.client.force_login(self.owner)
        response = self.client.post(self._edit_url(reply), {"body": "After", "article": 999, "parent": ""})
        self.assertEqual(response.status_code, 302)
        reply.refresh_from_db()
        self.assertEqual(reply.body, "After")
        self.assertEqual(reply.article, self.article)
        self.assertEqual(reply.parent, parent)
        self.assertEqual(reply.user, self.owner)
        self.assertFalse(reply.is_approved)

    def test_other_user_cannot_edit_and_anonymous_is_redirected(self):
        comment = Comment.objects.create(article=self.article, user=self.owner, body="Original", is_approved=True)
        anonymous = self.client.get(self._edit_url(comment))
        self.assertEqual(anonymous.status_code, 302)
        self.client.force_login(self.other)
        response = self.client.post(self._edit_url(comment), {"body": "Attack"})
        self.assertEqual(response.status_code, 404)
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Original")

    def test_get_displays_form_but_never_mutates_and_post_obeys_csrf(self):
        comment = Comment.objects.create(article=self.article, user=self.owner, body="Original", is_approved=False)
        self.client.force_login(self.owner)
        response = self.client.get(self._edit_url(comment), follow=True)
        self.assertContains(response, "Original")
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Original")

        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        csrf_client.get(self.article.get_absolute_url())
        response = csrf_client.post(self._edit_url(comment), {"body": "No token"})
        self.assertEqual(response.status_code, 403)
        comment.refresh_from_db()
        self.assertEqual(comment.body, "Original")

    def test_pending_edit_stays_pending_and_edited_text_is_escaped(self):
        comment = Comment.objects.create(article=self.article, user=self.owner, body="Before", is_approved=False)
        self.client.force_login(self.owner)
        self.client.post(self._edit_url(comment), {"body": "<script>alert(1)</script>"})
        comment.refresh_from_db()
        self.assertFalse(comment.is_approved)
        response = self.client.get(self.article.get_absolute_url())
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;", html=False)
        self.assertNotContains(response, "<script>alert(1)</script>", html=False)
        self.assertContains(response, "Edited")

    def test_persian_edit_ui_is_localized(self):
        comment = Comment.objects.create(article=self.article, user=self.owner, body="متن", is_approved=False)
        self.client.force_login(self.owner)
        with translation.override("fa"):
            response = self.client.get(self._edit_url(comment), follow=True)
        self.assertContains(response, "ویرایش")
        self.assertContains(response, "ذخیره")

    def test_htmx_comment_create_returns_only_discussion_workspace(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self.article.get_absolute_url(),
            {"body": "A pending comment"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="comments"')
        self.assertNotContains(response, "<html", html=False)
        self.assertTrue(Comment.objects.filter(article=self.article, user=self.owner, body="A pending comment").exists())

    def test_htmx_invalid_comment_keeps_discussion_workspace(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            self.article.get_absolute_url(),
            {"body": "   "},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="comments"')
        self.assertNotContains(response, "<html", html=False)


class ArticleCommentCooldownTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("cooldown-owner", password="secret")
        self.other_user = get_user_model().objects.create_user("cooldown-other", password="secret")
        self.article = Article.objects.create(
            title="Cooldown article", slug="cooldown-article", content="<p>Text</p>",
            status=ArticleStatus.PUBLISHED, published_at=timezone.now(),
        )
        self.parent = Comment.objects.create(
            article=self.article, user=self.other_user, body="Approved parent", is_approved=True,
        )

    def _age_past_cooldown(self, comment):
        Comment.objects.filter(pk=comment.pk).update(
            created_at=timezone.now() - timedelta(seconds=ARTICLE_DISCUSSION_COOLDOWN_SECONDS + 1),
        )

    def test_first_htmx_submission_succeeds_once_and_immediate_second_shows_wait_with_body(self):
        self.client.force_login(self.user)
        first = self.client.post(
            self.article.get_absolute_url(), {"body": "First comment"}, HTTP_HX_REQUEST="true",
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(Comment.objects.filter(user=self.user).count(), 1)

        blocked = self.client.post(
            self.article.get_absolute_url(), {"body": "Keep this text"}, HTTP_HX_REQUEST="true",
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, 'id="comments"')
        self.assertContains(blocked, "Please wait a moment")
        self.assertContains(blocked, "You can post again in")
        self.assertContains(blocked, "Keep this text")
        self.assertContains(blocked, 'data-comment-cooldown="')
        self.assertEqual(Comment.objects.filter(user=self.user).count(), 1)

    def test_submission_succeeds_after_cooldown_and_other_users_are_independent(self):
        self.client.force_login(self.user)
        first = self.client.post(
            self.article.get_absolute_url(), {"body": "First comment"}, HTTP_HX_REQUEST="true",
        )
        self.assertEqual(first.status_code, 200)
        first_comment = Comment.objects.get(user=self.user, body="First comment")
        first_comment.is_approved = True
        first_comment.save(update_fields=["is_approved"])
        self._age_past_cooldown(first_comment)

        retry = self.client.post(
            self.article.get_absolute_url(), {"body": "After cooldown"}, HTTP_HX_REQUEST="true",
        )
        self.assertEqual(retry.status_code, 200)
        self.assertTrue(Comment.objects.filter(user=self.user, body="After cooldown").exists())

        independent_user = get_user_model().objects.create_user("cooldown-independent", password="secret")
        other_client = self.client_class()
        other_client.force_login(independent_user)
        other = other_client.post(
            self.article.get_absolute_url(), {"body": "Independent user"}, HTTP_HX_REQUEST="true",
        )
        self.assertEqual(other.status_code, 200)
        self.assertTrue(Comment.objects.filter(user=independent_user, body="Independent user").exists())

    def test_reply_cooldown_preserves_reply_target_and_uses_reply_label(self):
        self.client.force_login(self.user)
        first = self.client.post(
            self.article.get_absolute_url(), {"body": "First reply", "reply_to": self.parent.pk}, HTTP_HX_REQUEST="true",
        )
        self.assertEqual(first.status_code, 200)
        blocked = self.client.post(
            self.article.get_absolute_url(), {"body": "Keep reply", "reply_to": self.parent.pk}, HTTP_HX_REQUEST="true",
        )
        self.assertContains(blocked, "Please wait a moment")
        self.assertContains(blocked, "Keep reply")
        self.assertContains(blocked, f'value="{self.parent.pk}"')
        self.assertContains(blocked, "Post reply")
        self.assertEqual(Comment.objects.filter(user=self.user, parent=self.parent).count(), 1)

    def test_edit_and_delete_are_not_subject_to_create_cooldown(self):
        comment = Comment.objects.create(article=self.article, user=self.user, body="Before", is_approved=False)
        self.client.force_login(self.user)
        edit = self.client.post(reverse("blog:comment_edit", args=[comment.pk]), {"body": "After"})
        self.assertRedirects(edit, f"{self.article.get_absolute_url()}#comment-{comment.pk}")
        comment.refresh_from_db()
        self.assertEqual(comment.body, "After")

        delete = self.client.post(reverse("blog:comment_delete", args=[comment.pk]))
        self.assertRedirects(delete, f"{self.article.get_absolute_url()}#comments")
        self.assertFalse(Comment.objects.filter(pk=comment.pk).exists())

    def test_fa_htmx_wait_feedback_is_localized(self):
        Comment.objects.create(article=self.article, user=self.user, body="First", is_approved=False)
        self.client.force_login(self.user)
        with translation.override("fa"):
            response = self.client.post(
                self.article.get_absolute_url(), {"body": "متن من"}, HTTP_HX_REQUEST="true",
            )
        self.assertContains(response, "لطفاً کمی صبر کنید")
        self.assertContains(response, "می‌توانید تا")


class PublicEditorialExperienceTests(LanguageIsolationMixin, TestCase):
    def setUp(self):
        super().setUp()
        # Keep the assertions for the default storefront explicit; dedicated
        # Persian tests below exercise the localized route separately.
        self.author = get_user_model().objects.create_user("editor", password="secret")
        self.category = BlogCategory.objects.create(name="Guides", name_fa="راهنماها", slug="guides")
        self.tag = BlogTag.objects.create(name="Care", name_fa="نگهداری", slug="care")
        self.articles = []
        for index in range(12):
            article = Article.objects.create(
                title=f"Editorial {index}", title_fa=f"مقاله {index}", slug=f"editorial-{index}",
                summary="A useful editorial summary.", summary_fa="خلاصهٔ مفید مقاله.",
                content='<p class="article-align-justify">Body</p><figure class="image article-image-medium"><img src="/media/a.jpg" alt="Alt"><figcaption>Caption</figcaption></figure>',
                content_fa='<p class="article-align-justify">متن فارسی</p>', author=self.author,
                category=self.category, status=ArticleStatus.PUBLISHED,
                published_at=timezone.now() - timedelta(minutes=index),
            )
            article.tags.add(self.tag)
            self.articles.append(article)

    def test_landing_is_paginated_and_preserves_category_search_and_tag(self):
        response = self.client.get(reverse("blog:article_list"), {"category": self.category.slug, "tag": self.tag.slug, "q": "Editorial"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 12)
        self.assertEqual(len(response.context["page_obj"]), 9)
        self.assertContains(response, "page=2")
        self.assertContains(response, "Guides")
        page_two = self.client.get(reverse("blog:article_list"), {"category": self.category.slug, "tag": self.tag.slug, "q": "Editorial", "page": 2})
        self.assertEqual(len(page_two.context["page_obj"]), 3)
        self.assertContains(page_two, "category=guides")
        self.assertContains(page_two, "tag=care")

    def test_new_search_form_resets_discovery_scope_and_finds_other_category(self):
        other_category = BlogCategory.objects.create(name="History", name_fa="تاریخ", slug="history")
        outside = Article.objects.create(
            title="Outside guide", title_fa="راهنمای تاریخ", slug="outside-guide",
            content="<p>History result</p>", category=other_category,
            status=ArticleStatus.PUBLISHED, published_at=timezone.now(),
        )
        response = self.client.get(reverse("blog:article_list"), {"q": "History"})
        self.assertContains(response, outside.title)
        self.assertEqual(response.context["selected_category"], "")
        self.assertEqual(response.context["selected_tag"], "")
        self.assertNotContains(response, 'name="category"', html=False)
        self.assertNotContains(response, 'name="tag"', html=False)
        self.assertNotContains(response, 'name="page"', html=False)

    def test_search_can_be_intentionally_narrowed_then_cleared(self):
        other_category = BlogCategory.objects.create(name="History", name_fa="تاریخ", slug="history")
        outside = Article.objects.create(
            title="Outside guide", title_fa="راهنمای تاریخ", slug="outside-guide-filtered",
            content="<p>History result</p>", category=other_category,
            status=ArticleStatus.PUBLISHED, published_at=timezone.now(),
        )
        narrowed = self.client.get(reverse("blog:article_list"), {"q": "History", "category": other_category.slug})
        self.assertContains(narrowed, outside.title)
        self.assertEqual(narrowed.context["selected_category"], other_category.slug)
        self.assertContains(narrowed, f'?category={other_category.slug}', html=False)
        self.assertContains(narrowed, "Clear search")
        cleared = self.client.get(reverse("blog:article_list"), {"category": other_category.slug})
        self.assertEqual(cleared.context["query"], "")
        self.assertEqual(cleared.context["selected_category"], other_category.slug)

    def test_empty_search_recovery_is_truthful_in_english_and_persian(self):
        response = self.client.get(reverse("blog:article_list"), {"q": "no-match", "category": self.category.slug})
        self.assertContains(response, "No articles found for “no-match”")
        self.assertContains(response, "Search all categories")
        self.assertContains(response, "?q=no-match", html=False)
        fa_response = self.client.get("/fa/articles/", {"q": "بی‌نتیجه"})
        self.assertContains(fa_response, "بی‌نتیجه")
        self.assertContains(fa_response, "پاک کردن جستجو")

    def test_discovery_uses_storefront_tokens_and_detail_hero_is_bounded(self):
        landing = self.client.get(reverse("blog:article_list"))
        self.assertContains(landing, "store-container")
        self.assertContains(landing, "bg-brand")
        self.assertNotContains(landing, "bg-emerald-700")
        detail = self.client.get(self.articles[0].get_absolute_url())
        self.assertContains(detail, "data-article-hero-shell")
        self.assertContains(detail, "max-w-5xl")

    def test_invalid_filters_and_page_fall_back_safely(self):
        response = self.client.get(reverse("blog:article_list"), {"category": "missing", "tag": "missing", "page": "999"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_category"], "")
        self.assertEqual(response.context["selected_tag"], "")
        self.assertEqual(response.context["page_obj"].number, 2)

    def test_landing_uses_real_published_categories_and_excludes_drafts(self):
        live = BlogCategory.objects.create(name="New category", name_fa="دسته تازه", slug="new-category")
        published = Article.objects.create(title="Live", slug="live-category", content="<p>Live</p>", category=live, status=ArticleStatus.PUBLISHED, published_at=timezone.now())
        Article.objects.create(title="Draft", slug="draft-category", content="<p>Draft</p>", category=live)
        response = self.client.get(reverse("blog:article_list"))
        self.assertContains(response, "New category")
        self.assertContains(self.client.get(reverse("blog:article_list"), {"category": live.slug}), published.title)
        self.assertNotContains(response, "Draft")

    def test_detail_has_no_permanent_sidebar_and_keeps_frozen_article_html(self):
        article = self.articles[0]
        response = self.client.get(article.get_absolute_url())
        self.assertContains(response, "article-align-justify")
        self.assertContains(response, "article-image-medium")
        self.assertContains(response, "Caption")
        self.assertNotContains(response, "Popular Tags")
        self.assertNotContains(response, "Latest Articles")
        self.assertNotContains(response, "Tile &amp; Interior Design Expert", html=False)

    def test_detail_uses_one_header_trust_surface_and_compact_continuation_variants(self):
        article = self.articles[0]
        response = self.client.get(article.get_absolute_url())
        self.assertContains(response, 'data-article-header', html=False)
        self.assertContains(response, 'data-article-trust', html=False)
        self.assertEqual(response.content.count(b'data-article-trust'), 1)
        self.assertContains(response, 'data-related-article-card', html=False)

        from products.models import Product
        product = Product.objects.create(name="Context product", slug="context-product", sku="CONTEXT-PRODUCT")
        article.related_products.add(product)
        response = self.client.get(article.get_absolute_url())
        self.assertContains(response, 'data-article-product-card', html=False)

    def test_detail_category_tags_and_related_articles_are_source_backed(self):
        article = self.articles[0]
        response = self.client.get(article.get_absolute_url())
        self.assertContains(response, "Guides")
        self.assertContains(response, "Care")
        related = response.context["related_articles"]
        self.assertLessEqual(len(related), 3)
        self.assertNotIn(article.pk, [item.pk for item in related])

    def test_detail_omits_empty_products_and_renders_real_related_product(self):
        article = self.articles[0]
        response = self.client.get(article.get_absolute_url())
        self.assertNotContains(response, "Products related to this article")
        from products.models import Product
        product = Product.objects.create(name="Article product", slug="article-product", sku="ARTICLE-PRODUCT")
        article.related_products.add(product)
        response = self.client.get(article.get_absolute_url())
        self.assertContains(response, "Products related to this article")
        self.assertContains(response, "Article product")

    def test_comments_render_only_approved_nested_replies_and_keep_private_email_hidden(self):
        article = self.articles[0]
        approved = Comment.objects.create(article=article, user=self.author, body="Approved", is_approved=True, email="private@example.test")
        Comment.objects.create(article=article, user=self.author, parent=approved, body="Reply", is_approved=True)
        Comment.objects.create(article=article, user=self.author, body="Hidden", is_approved=False)
        response = self.client.get(article.get_absolute_url())
        self.assertContains(response, "Approved")
        self.assertContains(response, "Reply")
        self.assertNotContains(response, "Hidden")
        self.assertNotContains(response, "private@example.test")

    def test_authenticated_comment_submission_announces_moderation_and_anonymous_gets_login_path(self):
        article = self.articles[0]
        anonymous = self.client.get(article.get_absolute_url())
        self.assertContains(anonymous, "Log in to comment")
        self.client.force_login(self.author)
        response = self.client.post(article.get_absolute_url(), {"body": "Awaiting review"}, follow=True)
        self.assertContains(response, "awaits approval")
        self.assertTrue(Comment.objects.filter(body="Awaiting review", is_approved=False).exists())

    def test_public_queries_are_bounded_without_sidebar_queries(self):
        with CaptureQueriesContext(connection) as landing_queries:
            self.client.get(reverse("blog:article_list"))
        with CaptureQueriesContext(connection) as detail_queries:
            self.client.get(self.articles[0].get_absolute_url())
        self.assertLessEqual(len(landing_queries), 12)
        self.assertLessEqual(len(detail_queries), 16)


class ArticleHtmlSafetyTests(TestCase):
    def test_ck_editor_semantics_are_preserved_and_idempotent(self):
        html = '<p class="article-lead text-justify">مقدمه <strong>Bold</strong> <em>Italic</em></p><h2>H2</h2><h3>H3</h3><ol><li>one</li></ol><ul><li>دو</li></ul><blockquote class="article-callout">Quote</blockquote><p class="article-small text-center">small</p><a href="/guide">internal</a><a href="https://example.com">external</a><table><thead><tr><th colspan="2">head</th></tr></thead><tbody><tr><td rowspan="2">cell</td></tr></tbody></table><figure class="article-image-medium image-style-align-center"><img src="/media/a.webp" alt="تصویر"><figcaption>caption</figcaption></figure>'
        clean = sanitize_article_html(html)
        for fragment in ('article-lead', 'text-justify', '<h2>', '<h3>', '<strong>', '<em>', '<ol>', '<ul>', '<blockquote', 'colspan="2"', 'rowspan="2"', '<figure', 'article-image-align-center', 'alt="تصویر"', '<figcaption>'):
            self.assertIn(fragment, clean)
        self.assertIn('rel="noopener noreferrer"', clean)
        self.assertEqual(clean, sanitize_article_html(clean))

    def test_unsafe_markup_is_removed(self):
        html = '<script>alert(1)</script><style>x</style><iframe src="x"></iframe><object>x</object><embed><svg onload="x"></svg><p onclick="x" style="color:red" class="rogue">ok</p><img src="javascript:alert(1)" onerror="x"><a href="JaVaScRiPt:alert(1)">bad</a><a href="data:text/html,x">data</a>'
        clean = sanitize_article_html(html).lower()
        for unsafe in ('script', 'style=', 'iframe', 'object', 'embed', 'svg', 'onclick', 'onerror', 'javascript:', 'data:', 'rogue'):
            self.assertNotIn(unsafe, clean)

    def test_save_and_legacy_render_use_same_sanitizer(self):
        article = Article.objects.create(title='Safe', slug='safe-html', content='<p onclick="x">safe</p><script>bad</script>', status=ArticleStatus.PUBLISHED, published_at=timezone.now())
        self.assertEqual(article.content, '<p>safe</p>')
        response = self.client.get(article.get_absolute_url())
        self.assertContains(response, '<p>safe</p>', html=False)
        self.assertNotContains(response, 'bad</script>', html=False)

    def test_actual_ckeditor_alignment_output_is_canonicalized_for_ltr_and_rtl(self):
        actual = (
            '<p>start</p><p style="text-align:center;">center</p>'
            '<p style="text-align:right;">end</p>'
            '<p style="text-align:justify;">justify</p>'
        )
        ltr = sanitize_article_html(actual, direction="ltr")
        self.assertIn('<p>start</p>', ltr)
        self.assertIn('class="article-align-center"', ltr)
        self.assertIn('class="article-align-end"', ltr)
        self.assertIn('class="article-align-justify"', ltr)
        self.assertNotIn("style=", ltr)

        rtl = sanitize_article_html(
            '<p style="text-align:right;">شروع</p><p style="text-align:left;">پایان</p>',
            direction="rtl",
        )
        self.assertIn('class="article-align-start"', rtl)
        self.assertIn('class="article-align-end"', rtl)

    def test_actual_ckeditor_image_output_maps_only_finite_resize_presets(self):
        cases = {
            "32%": "article-image-small",
            "50%": "article-image-medium",
            "75%": "article-image-content",
            "100%": "article-image-wide",
        }
        for width, expected_class in cases.items():
            with self.subTest(width=width):
                actual = (
                    f'<figure class="image image_resized" style="width:{width};">'
                    '<img style="aspect-ratio:250/337;" src="/media/a.jpg" '
                    'width="250" height="337" alt="Alternative">'
                    '<figcaption>Caption</figcaption></figure>'
                )
                clean = sanitize_article_html(actual)
                self.assertIn(f'class="image {expected_class}"', clean)
                self.assertIn('alt="Alternative"', clean)
                self.assertIn('<figcaption>Caption</figcaption>', clean)
                self.assertNotIn("style=", clean)
                self.assertNotIn("image_resized", clean)
                self.assertEqual(clean, sanitize_article_html(clean))

        arbitrary = sanitize_article_html(
            '<figure class="image image_resized" style="width:56.83%;color:red">'
            '<img src="/media/a.jpg"></figure>'
        )
        self.assertEqual(arbitrary, '<figure class="image"><img src="/media/a.jpg"></figure>')

    def test_actual_ckeditor_block_alignment_is_directionally_canonicalized(self):
        actual = '<figure class="image article-image-medium image-style-block-align-right"><img src="/media/a.jpg"></figure>'
        ltr = sanitize_article_html(actual, direction="ltr")
        rtl = sanitize_article_html(actual, direction="rtl")
        self.assertIn('article-image-medium', ltr)
        self.assertIn('article-image-align-end', ltr)
        self.assertIn('article-image-align-start', rtl)
        self.assertNotIn('image-style-block-align-right', ltr)
        self.assertEqual(ltr, sanitize_article_html(ltr, direction="ltr"))

    def test_normalizer_is_deterministic_idempotent_and_does_not_pass_arbitrary_css(self):
        actual = '<p style="text-align:justify;color:red" onclick="x">متن Mixed</p>'
        once = normalize_article_editor_html(actual, direction="rtl")
        self.assertEqual(once, '<p onclick="x" class="article-align-justify">متن Mixed</p>')
        self.assertEqual(once, normalize_article_editor_html(once, direction="rtl"))
        clean = sanitize_article_html(actual, direction="rtl")
        self.assertEqual(clean, '<p class="article-align-justify">متن Mixed</p>')

    def test_double_save_preserves_semantic_alignment_image_size_caption_and_alt(self):
        actual = (
            '<p style="text-align:justify;">Stable</p>'
            '<figure class="image image_resized article-image-align-end" style="width:50%;">'
            '<img src="/media/a.jpg" alt="Alt"><figcaption>Caption</figcaption></figure>'
        )
        article = Article.objects.create(title="Stable", slug="stable-formatting", content=actual)
        first = article.content
        article.save()
        article.refresh_from_db()
        self.assertEqual(article.content, first)
        self.assertIn("article-align-justify", first)
        self.assertIn("article-image-medium", first)
        self.assertIn("article-image-align-end", first)
        self.assertIn("<figcaption>Caption</figcaption>", first)
