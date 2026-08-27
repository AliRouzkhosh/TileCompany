from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from io import BytesIO
from PIL import Image

from blog.models import Article, BlogCategory, Comment
from dashboard.management.commands.seed_dashboard_roles import Command as SeedDashboardRolesCommand
from products.models import Category


def image_upload():
    image = Image.new("RGB", (1, 1), "white")
    payload = BytesIO()
    image.save(payload, format="PNG")
    return SimpleUploadedFile("image.png", payload.getvalue(), content_type="image/png")


class ContentModerationAuthorizationTests(TestCase):
    def setUp(self):
        SeedDashboardRolesCommand().handle()
        User = get_user_model()
        self.content = User.objects.create_user("content", password="secret")
        self.content.groups.add(Group.objects.get(name="Content Manager"))
        self.moderator = User.objects.create_user("moderator", password="secret")
        self.moderator.groups.add(Group.objects.get(name="Moderator"))
        self.legacy = User.objects.create_user("legacy", password="secret", is_staff=True)
        self.customer = User.objects.create_user("customer", password="secret")
        self.category = BlogCategory.objects.create(name="Guides", slug="guides")
        self.article = Article.objects.create(title="Draft", slug="draft", content="<p>x</p>", author=self.content, category=self.category)

    def test_content_manager_non_staff_has_content_not_moderation_or_products(self):
        self.client.force_login(self.content)
        self.assertFalse(self.content.is_staff)
        self.assertEqual(self.client.get(reverse("dashboard:blog:catalog")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:storefront:media")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:moderation")).status_code, 403)
        self.assertEqual(self.client.get(reverse("dashboard:products:edit", args=[99999])).status_code, 403)

    def test_article_publication_is_not_in_change_permission(self):
        editor = get_user_model().objects.create_user("narrow", password="secret")
        editor.user_permissions.add(Permission.objects.get(content_type__app_label="blog", codename="change_article"))
        self.client.force_login(editor)
        response = self.client.post(reverse("dashboard:blog:edit", args=[self.article.pk]), {
            "title": self.article.title, "slug": self.article.slug, "content": "<p>changed</p>",
            "author": self.content.pk, "status": "published", "published_at": "",
            "attachments-TOTAL_FORMS": "0", "attachments-INITIAL_FORMS": "0", "attachments-MIN_NUM_FORMS": "0", "attachments-MAX_NUM_FORMS": "1000",
        })
        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.status, "draft")
        self.assertEqual(self.client.post(reverse("dashboard:blog:publish", args=[self.article.pk])).status_code, 403)

    def test_upload_uses_article_permission_not_staff(self):
        upload_url = reverse("dashboard:ckeditor_upload")
        self.client.force_login(self.content)
        response = self.client.post(upload_url, {"upload": image_upload()})
        self.assertEqual(response.status_code, 200)
        self.assertIn("url", response.json())
        self.assertEqual(
            self.client.post("/ckeditor5/image_upload/", {"upload": image_upload()}).status_code,
            404,
        )
        for user in (self.customer, self.legacy, self.moderator):
            self.client.force_login(user)
            self.assertEqual(self.client.post(upload_url, {"upload": image_upload()}).status_code, 403)

    def test_editor_upload_rejects_svg_and_spoofed_image_content(self):
        self.client.force_login(self.content)
        upload_url = reverse("dashboard:ckeditor_upload")
        for upload in (
            SimpleUploadedFile("image.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", content_type="image/svg+xml"),
            SimpleUploadedFile("image.jpg", b"not an image", content_type="image/jpeg"),
        ):
            with self.subTest(name=upload.name):
                self.assertEqual(self.client.post(upload_url, {"upload": upload}).status_code, 400)

    def test_public_blog_comment_delete_uses_moderation_permission_not_staff(self):
        comment = Comment.objects.create(article=self.article, user=self.customer, body="customer")
        self.client.force_login(self.legacy)
        self.assertEqual(self.client.post(reverse("blog:comment_delete", args=[comment.pk])).status_code, 403)
        self.client.force_login(self.moderator)
        self.assertEqual(self.client.post(reverse("blog:comment_delete", args=[comment.pk])).status_code, 302)

    def test_split_moderation_capabilities(self):
        blog_only = get_user_model().objects.create_user("blog-mod", password="secret")
        for codename in ("view_comment", "moderate_article_comments"):
            blog_only.user_permissions.add(Permission.objects.get(content_type__app_label="blog", codename=codename))
        self.client.force_login(blog_only)
        self.assertEqual(self.client.get(reverse("dashboard:moderation")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:blog_comment_queue")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:review_queue")).status_code, 403)
