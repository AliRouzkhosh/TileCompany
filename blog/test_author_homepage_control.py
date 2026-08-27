from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation

from blog.models import Article, ArticleStatus
from dashboard.forms_blog import ArticleForm
from dashboard.forms_storefront import HomepageEditorialSelectionForm
from dashboard.management.commands.seed_dashboard_roles import Command as SeedRolesCommand
from products.models import HomepageEditorialSelection
from products.selectors import get_homepage_editorial_article


class ArticleAuthorPresentationTests(TestCase):
    def setUp(self):
        SeedRolesCommand().handle()
        User = get_user_model()
        self.named = User.objects.create_user(
            "ali123", password="secret", email="private-author@example.com",
            first_name="Ali", last_name="Rouzkhosh", is_staff=True
        )
        self.named.groups.add(Group.objects.get(name="Content Manager"))
        self.username_only = User.objects.create_user("plainwriter", password="secret", is_staff=True)
        self.username_only.groups.add(Group.objects.get(name="Content Manager"))
        self.customer = User.objects.create_user("customer", password="secret")

    def make_article(self, slug, author):
        return Article.objects.create(
            title=f"Article {slug}", title_fa=f"مقاله {slug}", slug=slug,
            content="<p>Body</p>", content_fa="<p>محتوا</p>", author=author,
            status=ArticleStatus.PUBLISHED, published_at=timezone.now(),
        )

    def test_public_author_uses_full_name_then_username_and_handles_none(self):
        named = self.make_article("named", self.named)
        username = self.make_article("username", self.username_only)
        anonymous = self.make_article("no-author", None)

        self.assertContains(self.client.get(named.get_absolute_url()), "Ali Rouzkhosh")
        self.assertNotContains(self.client.get(named.get_absolute_url()), self.named.email)
        self.assertContains(self.client.get(username.get_absolute_url()), "plainwriter")
        self.assertNotContains(self.client.get(anonymous.get_absolute_url()), "Written by")

    def test_dashboard_author_labels_order_and_eligibility(self):
        form = ArticleForm(user=self.named)
        field = form.fields["author"]
        authors = list(field.queryset)
        self.assertNotIn(self.customer, authors)
        self.assertEqual(authors, sorted(authors, key=lambda user: (user.first_name, user.last_name, user.username, user.pk)))
        self.assertEqual(field.label_from_instance(self.named), "Ali Rouzkhosh (@ali123)")
        self.assertEqual(field.label_from_instance(self.username_only), "plainwriter")


class HomepageEditorialControlTests(TestCase):
    def setUp(self):
        SeedRolesCommand().handle()
        User = get_user_model()
        self.editor = User.objects.create_user("homepage-editor", password="secret", is_staff=True)
        self.editor.groups.add(Group.objects.get(name="Content Manager"))

    def article(self, slug, *, days=0, status=ArticleStatus.PUBLISHED, title_fa="عنوان فارسی"):
        return Article.objects.create(
            title=f"Article {slug}", title_fa=title_fa, slug=slug,
            summary=f"Summary {slug}", summary_fa=f"خلاصه {slug}",
            content="<p>Body</p>", content_fa="<p>محتوا</p>",
            status=status,
            published_at=timezone.now() - timedelta(days=days) if status == ArticleStatus.PUBLISHED else None,
        )

    def select(self, article):
        self.client.force_login(self.editor)
        return self.client.post(reverse("dashboard:storefront:update_editorial"), {"article": article.pk if article else ""})

    def test_explicit_selection_change_clear_and_unpublished_fallback(self):
        automatic = self.article("automatic", days=0)
        selected = self.article("selected", days=2)
        other = self.article("other", days=3)

        self.assertEqual(self.client.get("/en/").context["editorial_article"], automatic)
        self.assertEqual(self.select(selected).status_code, 302)
        self.assertEqual(self.client.get("/en/").context["editorial_article"], selected)
        self.select(other)
        self.assertEqual(self.client.get("/en/").context["editorial_article"], other)
        self.select(None)
        self.assertEqual(self.client.get("/en/").context["editorial_article"], automatic)

        self.select(selected)
        selected.status = ArticleStatus.DRAFT
        selected.save(update_fields=("status",))
        homepage = self.client.get("/en/")
        self.assertEqual(homepage.context["editorial_article"], automatic)
        self.assertNotContains(homepage, selected.title)

    def test_selector_only_offers_currently_published_articles_and_fa_renders(self):
        published = self.article("published", title_fa="مقاله منتخب")
        draft = self.article("draft", status=ArticleStatus.DRAFT)
        scheduled = self.article("scheduled")
        scheduled.published_at = timezone.now() + timedelta(days=1)
        scheduled.save(update_fields=("published_at",))

        form = HomepageEditorialSelectionForm()
        self.assertIn(published, form.fields["article"].queryset)
        self.assertNotIn(draft, form.fields["article"].queryset)
        self.assertNotIn(scheduled, form.fields["article"].queryset)
        self.select(published)
        with translation.override("fa"):
            self.assertContains(self.client.get("/fa/"), "مقاله منتخب")

    def test_customer_cannot_change_selection(self):
        article = self.article("protected")
        customer = get_user_model().objects.create_user("homepage-customer", password="secret")
        self.client.force_login(customer)
        response = self.client.post(reverse("dashboard:storefront:update_editorial"), {"article": article.pk})
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(HomepageEditorialSelection.objects.exists())

    def test_selector_query_cost_is_bounded(self):
        selected = self.article("selected", title_fa="منتخب")
        self.article("fallback", title_fa="جایگزین")

        with CaptureQueriesContext(connection) as automatic_queries:
            self.assertIsNotNone(get_homepage_editorial_article())
        self.assertEqual(len(automatic_queries), 3)

        HomepageEditorialSelection.objects.create(article=selected)
        with CaptureQueriesContext(connection) as explicit_queries:
            self.assertEqual(get_homepage_editorial_article(), selected)
        self.assertEqual(len(explicit_queries), 1)
