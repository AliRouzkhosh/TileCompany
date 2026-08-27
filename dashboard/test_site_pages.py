from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.html import escape
from django.utils.translation import override

from blog.models import SitePage, SitePageKey
from dashboard.role_definitions import ROLE_DEFINITIONS
from dashboard.forms_blog import SitePageForm


class SitePageDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_dashboard_roles", verbosity=0)
        User = get_user_model()
        cls.customer = User.objects.create_user("customer", password="secret")
        cls.staff_without_permission = User.objects.create_user(
            "unrelated-staff", password="secret", is_staff=True,
        )
        cls.content_manager = User.objects.create_user("content-editor", password="secret")
        cls.content_manager.groups.add(Group.objects.get(name="Content Manager"))
        cls.owner = User.objects.create_superuser("owner", "owner@example.com", "secret")

    def page(self, key=SitePageKey.ABOUT):
        return SitePage.objects.get(key=key)

    def data(self, **overrides):
        data = {
            "title": "Temporary About",
            "title_fa": "دربارهٔ موقت",
            "summary": "Temporary summary",
            "summary_fa": "خلاصهٔ موقت",
            "content": "<h2>English heading</h2><p>English temporary content.</p>",
            "content_fa": "<h2>عنوان فارسی</h2><p>محتوای موقت فارسی.</p>",
            "meta_title": "",
            "meta_title_fa": "",
            "meta_description": "",
            "meta_description_fa": "",
        }
        data.update(overrides)
        return data

    def login(self, user=None):
        self.client.force_login(user or self.content_manager)

    def test_list_authorization_and_exact_canonical_rows(self):
        url = reverse("dashboard:blog:pages")
        self.assertEqual(self.client.get(url).status_code, 302)
        for user in (self.customer, self.staff_without_permission):
            with self.subTest(user=user.username):
                self.login(user)
                self.assertEqual(self.client.get(url).status_code, 403)

        for user in (self.content_manager, self.owner):
            with self.subTest(user=user.username):
                self.login(user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.context["site_pages"]), 6)
                self.assertEqual(
                    [page.key for page in response.context["site_pages"]],
                    list(SitePageKey.values),
                )

    def test_list_has_fixed_identity_labels_edit_preview_and_no_add_delete_controls(self):
        self.login()
        response = self.client.get(reverse("dashboard:blog:pages"))
        for label in (
            "About", "Privacy", "Terms & Conditions", "Shipping & Delivery",
            "Returns & Refunds", "Payment Information",
        ):
            self.assertContains(response, escape(label))
        for page in response.context["site_pages"]:
            self.assertContains(response, reverse("dashboard:blog:page_edit", args=[page.pk]))
            self.assertContains(response, reverse("dashboard:blog:page_preview", args=[page.pk]))
        source = Path("dashboard/templates/dashboard/blog/site_page_list.html").read_text(encoding="utf-8")
        self.assertNotIn("page_create", source)
        self.assertNotIn("page_delete", source)
        with self.assertRaises(NoReverseMatch):
            reverse("dashboard:blog:page_create")
        with self.assertRaises(NoReverseMatch):
            reverse("dashboard:blog:page_delete", args=[self.page().pk])

    def test_content_manager_can_save_incomplete_draft_and_key_is_not_post_editable(self):
        self.login()
        page = self.page()
        data = self.data(
            title="Draft title", title_fa="", content="", content_fa="", key="privacy",
        )
        response = self.client.post(reverse("dashboard:blog:page_edit", args=[page.pk]), data)
        self.assertRedirects(response, reverse("dashboard:blog:page_edit", args=[page.pk]))
        page.refresh_from_db()
        self.assertEqual(page.key, SitePageKey.ABOUT)
        self.assertEqual(page.title, "Draft title")
        self.assertFalse(page.is_published)

    def test_form_exposes_only_content_and_publication_fields(self):
        form = SitePageForm(instance=self.page())
        self.assertEqual(
            tuple(form.fields),
            (
                "title", "title_fa", "summary", "summary_fa", "content", "content_fa",
                "is_published", "meta_title", "meta_title_fa", "meta_description",
                "meta_description_fa",
            ),
        )
        self.login()
        response = self.client.get(reverse("dashboard:blog:page_edit", args=[self.page().pk]))
        self.assertNotContains(response, 'name="key"')
        self.assertNotContains(response, 'name="published_at"')
        self.assertNotContains(response, 'name="updated_at"')

    def test_fa_list_localizes_navigation_identity_and_states(self):
        self.login()
        with override("fa"):
            response = self.client.get(reverse("dashboard:blog:pages"))
        self.assertContains(response, '<html lang="fa" dir="rtl">')
        for label in (
            "صفحه‌ها", "پیش‌نویس", "دربارهٔ ما", "حریم خصوصی", "شرایط و ضوابط",
            "ارسال و تحویل", "مرجوعی و بازپرداخت", "اطلاعات پرداخت",
        ):
            self.assertContains(response, label)

    def test_publication_requires_complete_bilingual_fields_with_localized_errors(self):
        self.login()
        page = self.page()
        with override("fa"):
            url = reverse("dashboard:blog:page_edit", args=[page.pk])
            response = self.client.post(
                url,
                self.data(title_fa="", content_fa="", is_published="on"),
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "عنوان فارسی را پیش از انتشار وارد کنید")
        self.assertContains(response, "محتوای فارسی را پیش از انتشار وارد کنید")
        self.assertNotContains(response, "This bilingual field is required before publication.")
        page.refresh_from_db()
        self.assertFalse(page.is_published)

    def test_unauthorized_crafted_post_cannot_mutate_page(self):
        page = self.page()
        url = reverse("dashboard:blog:page_edit", args=[page.pk])
        for user in (self.customer, self.staff_without_permission):
            with self.subTest(user=user.username):
                self.login(user)
                self.assertEqual(self.client.post(url, self.data(title="Attack")).status_code, 403)
                page.refresh_from_db()
                self.assertEqual(page.title, "")

    def test_edit_sanitizes_both_languages_and_retains_safe_rich_html(self):
        self.login()
        page = self.page()
        response = self.client.post(
            reverse("dashboard:blog:page_edit", args=[page.pk]),
            self.data(
                content='<h2 onclick="x()">Safe</h2><a href="javascript:x()">Bad</a><ul><li>Item</li></ul><script>x()</script>',
                content_fa='<h2 onclick="x()">امن</h2><a href="javascript:x()">بد</a><ul><li>مورد</li></ul><script>x()</script>',
            ),
        )
        self.assertEqual(response.status_code, 302)
        page.refresh_from_db()
        for content in (page.content, page.content_fa):
            self.assertNotIn("onclick", content)
            self.assertNotIn("javascript:", content)
            self.assertNotIn("<script", content)
            self.assertIn("<h2>", content)
            self.assertIn("<ul><li>", content)

    def test_preview_authorization_locale_and_public_separation(self):
        page = self.page()
        page.title = "English draft"
        page.title_fa = "پیش‌نویس فارسی"
        page.summary = "English draft summary"
        page.summary_fa = "خلاصهٔ پیش‌نویس فارسی"
        page.content = "<p>Private English draft</p>"
        page.content_fa = "<p>پیش‌نویس خصوصی فارسی</p>"
        page.save()

        with override("en"):
            preview_url = reverse("dashboard:blog:page_preview", args=[page.pk])
            public_url = reverse("sitepages:about")
        self.assertEqual(self.client.get(preview_url).status_code, 302)
        for user in (self.customer, self.staff_without_permission):
            self.login(user)
            self.assertEqual(self.client.get(preview_url).status_code, 403)

        for user in (self.content_manager, self.owner):
            self.login(user)
            english = self.client.get(preview_url)
            self.assertContains(english, "English draft")
            self.assertContains(english, "Private English draft")
            self.assertNotContains(english, "پیش‌نویس خصوصی فارسی")
            self.assertEqual(self.client.get(public_url).status_code, 404)

            with override("fa"):
                persian = self.client.get(reverse("dashboard:blog:page_preview", args=[page.pk]))
            self.assertContains(persian, '<html lang="fa" dir="rtl">')
            self.assertContains(persian, "پیش‌نویس فارسی")
            self.assertContains(persian, "پیش‌نویس خصوصی فارسی")
            self.assertNotContains(persian, "Private English draft")

    def test_dashboard_publish_unpublish_drives_public_route_and_footer(self):
        self.login()
        page = self.page()
        edit_url = reverse("dashboard:blog:page_edit", args=[page.pk])
        public_url = reverse("sitepages:about")

        publish_data = self.data(is_published="on")
        self.assertEqual(self.client.post(edit_url, publish_data).status_code, 302)
        page.refresh_from_db()
        first_published_at = page.published_at
        self.assertIsNotNone(first_published_at)
        self.assertEqual(self.client.get(public_url).status_code, 200)
        self.assertContains(self.client.get(reverse("products:home")), public_url, count=2)

        self.assertEqual(self.client.post(edit_url, self.data()).status_code, 302)
        page.refresh_from_db()
        self.assertFalse(page.is_published)
        self.assertEqual(page.published_at, first_published_at)
        self.assertEqual(self.client.get(public_url).status_code, 404)
        self.assertNotContains(self.client.get(reverse("products:home")), public_url)

        self.assertEqual(self.client.post(edit_url, publish_data).status_code, 302)
        page.refresh_from_db()
        self.assertEqual(page.published_at, first_published_at)

    def test_preview_get_does_not_mutate_page(self):
        self.login()
        page = self.page()
        before = (page.is_published, page.published_at, page.updated_at)
        self.assertEqual(
            self.client.get(reverse("dashboard:blog:page_preview", args=[page.pk])).status_code,
            200,
        )
        page.refresh_from_db()
        self.assertEqual((page.is_published, page.published_at, page.updated_at), before)

    def test_list_sitepage_queries_are_bounded(self):
        self.login()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("dashboard:blog:pages"))
        self.assertEqual(response.status_code, 200)
        site_page_queries = [
            query for query in captured.captured_queries
            if "blog_sitepage" in query["sql"].lower()
        ]
        self.assertEqual(len(site_page_queries), 2)  # list + frozen one-query footer context


class SitePageRoleTests(TestCase):
    def test_content_manager_has_only_view_and_change_sitepage_permissions(self):
        call_command("seed_dashboard_roles", verbosity=0)
        expected = {"blog.view_sitepage", "blog.change_sitepage"}
        content_permissions = ROLE_DEFINITIONS["Content Manager"]
        self.assertEqual(len(content_permissions), 22)
        self.assertEqual(
            {permission for permission in content_permissions if permission.endswith("_sitepage")},
            expected,
        )
        group = Group.objects.get(name="Content Manager")
        actual = {
            f"{permission.content_type.app_label}.{permission.codename}"
            for permission in group.permissions.select_related("content_type")
            if permission.codename.endswith("_sitepage")
        }
        self.assertEqual(actual, expected)
        self.assertNotIn("blog.add_sitepage", content_permissions)
        self.assertNotIn("blog.delete_sitepage", content_permissions)
        for role_name, permissions in ROLE_DEFINITIONS.items():
            if role_name != "Content Manager":
                self.assertTrue(expected.isdisjoint(permissions))
