from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.translation import override

from .context_processors import FOOTER_SITE_PAGE_ROUTES
from .models import SitePage, SitePageKey
from .selectors import get_published_site_page


class SitePagePublicTests(TestCase):
    def page(self, key=SitePageKey.ABOUT):
        return SitePage.objects.get(key=key)

    def publish(self, key=SitePageKey.ABOUT, **overrides):
        page = self.page(key)
        page.title = overrides.get("title", f"English {key}")
        page.title_fa = overrides.get("title_fa", f"عنوان فارسی {key}")
        page.summary = overrides.get("summary", "English summary")
        page.summary_fa = overrides.get("summary_fa", "خلاصه فارسی")
        page.content = overrides.get("content", "<h2>Safe heading</h2><p>English body</p>")
        page.content_fa = overrides.get("content_fa", "<h2>عنوان امن</h2><p>متن فارسی</p>")
        page.meta_title = overrides.get("meta_title", "")
        page.meta_title_fa = overrides.get("meta_title_fa", "")
        page.meta_description = overrides.get("meta_description", "")
        page.meta_description_fa = overrides.get("meta_description_fa", "")
        page.is_published = True
        page.save()
        return page

    def get_localized(self, language, route_name):
        with override(language):
            return self.client.get(reverse(route_name))

    def test_each_explicit_route_maps_to_its_fixed_key(self):
        for key, (route_name, _label) in FOOTER_SITE_PAGE_ROUTES.items():
            with self.subTest(key=key):
                page = self.publish(key)
                response = self.get_localized("en", route_name)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["site_page"].pk, page.pk)
                page.is_published = False
                page.save()

    def test_draft_is_404_for_anonymous_staff_and_superuser(self):
        with override("en"):
            url = reverse("sitepages:privacy")
        self.assertEqual(self.client.get(url).status_code, 404)
        staff = User.objects.create_user("staff", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 404)
        owner = User.objects.create_superuser("owner", "owner@example.com", "pw")
        self.client.force_login(owner)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_selector_rejects_drafts_and_unknown_keys(self):
        from django.http import Http404

        with self.assertRaises(Http404):
            get_published_site_page(SitePageKey.ABOUT)
        with self.assertRaises(Http404):
            get_published_site_page("not-canonical")
        page = self.publish()
        self.assertEqual(get_published_site_page(SitePageKey.ABOUT), page)

    def test_publication_transitions_update_route_and_footer_immediately(self):
        with override("en"):
            url = reverse("sitepages:privacy")
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotContains(self.client.get("/en/"), url)

        page = self.publish(SitePageKey.PRIVACY)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get("/en/"), url, count=2)

        page.is_published = False
        page.save()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotContains(self.client.get("/en/"), url)

        page.is_published = True
        page.save()
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_en_fa_fields_and_seo_hooks_are_strictly_localized(self):
        self.publish(
            title="English page title",
            title_fa="عنوان صفحه فارسی",
            summary="English page summary",
            summary_fa="خلاصه صفحه فارسی",
            content="<p>Unique English content</p>",
            content_fa="<p>محتوای منحصربه‌فرد فارسی</p>",
            meta_title="English SEO title",
            meta_title_fa="عنوان سئوی فارسی",
            meta_description="English SEO description",
            meta_description_fa="توضیح سئوی فارسی",
        )
        english = self.get_localized("en", "sitepages:about")
        self.assertContains(english, "English page title")
        self.assertContains(english, "English page summary")
        self.assertContains(english, "Unique English content")
        self.assertContains(english, "<title>English SEO title</title>", html=True)
        self.assertContains(english, 'content="English SEO description"')
        self.assertNotContains(english, "محتوای منحصربه‌فرد فارسی")

        persian = self.get_localized("fa", "sitepages:about")
        self.assertContains(persian, '<html lang="fa" dir="rtl">')
        self.assertContains(persian, "عنوان صفحه فارسی")
        self.assertContains(persian, "خلاصه صفحه فارسی")
        self.assertContains(persian, "محتوای منحصربه‌فرد فارسی")
        self.assertContains(persian, "<title>عنوان سئوی فارسی</title>", html=True)
        self.assertContains(persian, 'content="توضیح سئوی فارسی"')
        self.assertNotContains(persian, "Unique English content")

    def test_title_falls_back_to_localized_title_and_description_to_base(self):
        self.publish(title="About title", title_fa="عنوان درباره")
        english = self.get_localized("en", "sitepages:about")
        self.assertContains(english, "<title>About title</title>", html=True)
        self.assertContains(
            english,
            "Tiles, ceramics, installation materials, and practical support for every project.",
        )

    def test_sanitized_rich_html_renders_without_executable_markup_or_internal_state(self):
        page = self.publish(
            content=(
                '<h2 onclick="alert(1)">Heading</h2><ul><li><strong>Item</strong></li></ul>'
                '<a href="javascript:alert(2)">Unsafe</a><a href="https://example.com">Safe</a>'
                '<script>alert(3)</script>'
            ),
        )
        response = self.get_localized("en", "sitepages:about")
        self.assertContains(response, "<h2>Heading</h2>", html=True)
        self.assertContains(response, "<ul><li><strong>Item</strong></li></ul>", html=True)
        self.assertContains(response, 'href="https://example.com"')
        self.assertNotContains(response, "onclick")
        self.assertNotContains(response, "javascript:")
        self.assertNotContains(response, "<script>alert(3)</script>")
        self.assertNotContains(response, f'data-site-page-key="{page.key}"')
        self.assertNotContains(response, "is_published")

    def test_no_generic_public_site_page_endpoint_exists(self):
        self.assertEqual(self.client.get("/en/pages/about/").status_code, 404)
        self.assertEqual(self.client.get("/en/contact/").status_code, 404)
        self.assertEqual(self.client.get("/en/faq/").status_code, 404)


class SitePageFooterTests(TestCase):
    def publish(self, key):
        page = SitePage.objects.get(key=key)
        page.title = f"English {key}"
        page.title_fa = f"عنوان فارسی {key}"
        page.content = "<p>English body</p>"
        page.content_fa = "<p>متن فارسی</p>"
        page.is_published = True
        page.save()
        return page
    def site_page_query_count(self):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get("/en/")
            self.assertEqual(response.status_code, 200)
        return len([
            query for query in captured.captured_queries
            if "blog_sitepage" in query["sql"].lower()
        ])

    def test_footer_query_is_one_with_zero_or_all_published_pages(self):
        self.assertEqual(self.site_page_query_count(), 1)
        for key in SitePageKey.values:
            self.publish(key)
        self.assertEqual(self.site_page_query_count(), 1)

    def test_footer_shows_only_published_fixed_localized_links(self):
        privacy = self.publish(SitePageKey.PRIVACY)
        privacy.title = "An operator title that is not navigation"
        privacy.save()
        self.publish(SitePageKey.TERMS)
        english = self.client.get("/en/")
        self.assertContains(english, ">Privacy<", count=2)
        self.assertContains(english, ">Terms &amp; Conditions<", count=2)
        self.assertNotContains(english, "An operator title that is not navigation")
        self.assertNotContains(english, reverse("sitepages:about"))
        self.assertContains(english, 'hx-boost="false"')

        with override("fa"):
            persian = self.client.get("/fa/")
        self.assertContains(persian, ">حریم خصوصی<", count=2)
        self.assertContains(persian, ">شرایط و ضوابط<", count=2)

    def test_footer_site_page_links_are_safe_cross_workspace_navigation(self):
        self.publish(SitePageKey.ABOUT)
        with override("en"):
            about_url = reverse("sitepages:about")
            routes = (
                reverse("products:home"),
                reverse("products:product_list"),
                reverse("blog:article_list"),
                reverse("support:help_list"),
                reverse("accounts:login"),
            )
        expected_link = f'href="{about_url}" hx-boost="false"'
        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, expected_link, count=2)
