################ blogs/models.py #######################
from math import ceil

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_ckeditor_5.fields import CKEditor5Field
from django.utils.html import strip_tags
from TileCompany.uploads import validate_pdf
from .sanitization import sanitize_article_html


class ArticleStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"


class SitePageKey(models.TextChoices):
    ABOUT = "about", "About"
    PRIVACY = "privacy", "Privacy"
    TERMS = "terms", "Terms"
    SHIPPING_DELIVERY = "shipping-delivery", "Shipping / Delivery"
    RETURNS_REFUNDS = "returns-refunds", "Returns / Refunds"
    PAYMENT_INFORMATION = "payment-information", "Payment Information"


class SitePage(models.Model):
    """Durable bilingual company/policy content with a fixed machine identity."""

    key = models.CharField(max_length=40, choices=SitePageKey.choices, unique=True)
    title = models.CharField(max_length=200, blank=True)
    title_fa = models.CharField(max_length=200, blank=True)
    summary = models.TextField(max_length=300, blank=True)
    summary_fa = models.TextField(max_length=300, blank=True)
    content = CKEditor5Field(config_name="default", blank=True)
    content_fa = CKEditor5Field(config_name="default", blank=True)
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    meta_title = models.CharField(max_length=255, blank=True)
    meta_title_fa = models.CharField(max_length=255, blank=True)
    meta_description = models.CharField(max_length=320, blank=True)
    meta_description_fa = models.CharField(max_length=320, blank=True)

    class Meta:
        ordering = ["key"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(key__in=SitePageKey.values),
                name="blog_sitepage_canonical_key",
            ),
        ]

    def __str__(self):
        return self.title or self.key

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        if "key" in field_names:
            instance._persisted_key = instance.key
        return instance

    def clean(self):
        super().clean()
        errors = {}
        if self.key not in SitePageKey.values:
            errors["key"] = _("Select a valid canonical page identity.")
        if self.is_published:
            required = {
                "title": self.title,
                "title_fa": self.title_fa,
                "content": strip_tags(self.content or ""),
                "content_fa": strip_tags(self.content_fa or ""),
            }
            for field_name, value in required.items():
                if not (value or "").strip():
                    errors[field_name] = _("This bilingual field is required before publication.")
        if errors:
            raise ValidationError(errors)

    def _validate_key_immutability(self):
        if self._state.adding:
            return
        original_key = getattr(self, "_persisted_key", None)
        if original_key is None:
            original_key = type(self).objects.only("key").get(pk=self.pk).key
        if self.key != original_key:
            raise ValidationError({"key": _("Site page identity cannot be changed after creation.")})

    def save(self, *args, **kwargs):
        self._validate_key_immutability()
        self.content = sanitize_article_html(self.content, direction="ltr")
        self.content_fa = sanitize_article_html(self.content_fa, direction="rtl")
        self.clean()

        if self.is_published and self.published_at is None:
            self.published_at = timezone.now()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"published_at"}

        super().save(*args, **kwargs)
        self._persisted_key = self.key


class BlogCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_fa = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    description_fa = models.TextField(blank=True)
    icon = models.CharField(
        max_length=50,
        blank=True,
    )
    color = models.CharField(
        max_length=20,
        default="emerald",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Category"
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name


class BlogTag(models.Model):
    name = models.CharField(max_length=50, unique=True)
    name_fa = models.CharField(max_length=50, blank=True)
    slug = models.SlugField(unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Article(models.Model):
    title = models.CharField(max_length=200)
    title_fa = models.CharField(max_length=200, blank=True)

    author = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="articles",
    )

    @property
    def author_display_name(self):
        """One public author identity contract; email is never a fallback."""
        if self.author is None:
            return ""
        return self.author.get_full_name().strip() or self.author.get_username()

    cover_image = models.ImageField(
        upload_to="blog/",
        blank=True,
        null=True,
    )

    summary = models.TextField(
        max_length=300,
        blank=True,
    )
    summary_fa = models.TextField(
        max_length=300,
        blank=True,
    )

    content = CKEditor5Field(
        config_name="default",
    )
    content_fa = CKEditor5Field(
        config_name="default",
        blank=True,
    )

    category = models.ForeignKey(
        BlogCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
    )

    tags = models.ManyToManyField(
        BlogTag,
        blank=True,
        related_name="articles",
    )

    related_products = models.ManyToManyField(
        "products.Product",
        blank=True,
        related_name="articles",
    )

    slug = models.SlugField(
        max_length=200,
        unique=True,
        db_index=True,
    )

    status = models.CharField(
        max_length=20,
        choices=ArticleStatus.choices,
        default=ArticleStatus.DRAFT,
        db_index=True,
    )

    published_at = models.DateTimeField(
        blank=True,
        null=True,
        db_index=True,
    )

    meta_title = models.CharField(
        max_length=255,
        blank=True,
    )
    meta_title_fa = models.CharField(
        max_length=255,
        blank=True,
    )

    meta_description = models.CharField(
        max_length=320,
        blank=True,
    )
    meta_description_fa = models.CharField(
        max_length=320,
        blank=True,
    )

    reading_time = models.PositiveSmallIntegerField(
        default=1,
        editable=False,
    )
    reading_time_fa = models.PositiveSmallIntegerField(default=1, editable=False)

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-published_at", "-created_at"]

        permissions = [
            ("publish_article", "Can publish or unpublish articles"),
        ]

        indexes = [
            models.Index(fields=["status", "-published_at"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse(
            "blog:article_detail",
            kwargs={"slug": self.slug},
        )

    def save(self, *args, **kwargs):
        # The public template intentionally renders sanitized rich text as HTML.
        self.content = sanitize_article_html(self.content, direction="ltr")
        self.content_fa = sanitize_article_html(self.content_fa, direction="rtl")
        self.reading_time = self._reading_time(self.content)
        self.reading_time_fa = self._reading_time(self.content_fa)

        if not self.meta_title:
            self.meta_title = self.title

        plain_text = strip_tags(self.content)

        if not self.meta_description:
            self.meta_description = (
                    self.summary
                    or plain_text[:160]
            )

        if (
            self.status == ArticleStatus.PUBLISHED
            and self.published_at is None
        ):
            self.published_at = timezone.now()

        super().save(*args, **kwargs)

    @property
    def sanitized_content(self):
        return sanitize_article_html(self.content, direction="ltr")

    @property
    def sanitized_content_fa(self):
        return sanitize_article_html(self.content_fa, direction="rtl")

    @staticmethod
    def _reading_time(content):
        return max(1, ceil(len(strip_tags(content or "").split()) / 200))

    @property
    def excerpt(self):
        if self.summary:
            return self.summary

        return strip_tags(self.content)[:180]

    @property
    def is_published(self):
        return bool(self.status == ArticleStatus.PUBLISHED and self.published_at and self.published_at <= timezone.now())

    @property
    def editorial_state(self):
        if self.status == ArticleStatus.DRAFT:
            return "draft"
        return "published" if self.is_published else "scheduled"

    @property
    def meta_title_fa_display(self):
        return self.meta_title_fa or self.title_fa

    @property
    def meta_description_fa_display(self):
        return self.meta_description_fa or self.summary_fa or strip_tags(self.content_fa)[:160]


def validate_pdf_upload(upload):
    """Validate PDF identity and size; this is not malware scanning."""
    validate_pdf(upload, max_bytes=10 * 1024 * 1024)


class ArticleAttachment(models.Model):
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="blog/attachments/", validators=[validate_pdf_upload])
    title = models.CharField(max_length=200)
    title_fa = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    description_fa = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sort_order", "uploaded_at")

    def __str__(self):
        return self.title

class Comment(models.Model):
    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name="comments",
    )

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
    )

    name = models.CharField(
        max_length=120,
        blank=True,
    )

    email = models.EmailField(
        blank=True,
    )

    body = models.TextField(
        max_length=1000,
    )

    is_approved = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    edited_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["created_at"]
        permissions = [
            ("moderate_article_comments", "Can moderate article comments"),
        ]

    def __str__(self):
        who = self.user.username if self.user else (self.name or "Guest")
        return f"Comment by {who} on {self.article.title}"
