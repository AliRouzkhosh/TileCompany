################ dashboard/forms_blog.py #############
from django import forms
from django.contrib.auth import get_user_model
from django.forms import inlineformset_factory
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django_ckeditor_5.widgets import CKEditor5Widget

from TileCompany.uploads import validate_raster_image
from blog.models import Article, ArticleAttachment, ArticleStatus, BlogCategory, BlogTag, SitePage


INPUT_CLASS = "w-full rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 shadow-sm transition focus:border-slate-900 focus:outline-none focus:ring-2 focus:ring-slate-200"
TEXTAREA_CLASS = INPUT_CLASS + " min-h-28 resize-y"
SELECT_CLASS = INPUT_CLASS


class DashboardStyledModelForm(forms.ModelForm):
    """Apply the dashboard's field treatment without overriding rich widgets."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "h-4 w-4 rounded border-slate-300 text-slate-900 focus:ring-slate-900")
            elif isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", TEXTAREA_CLASS)
            else:
                field.widget.attrs.setdefault("class", INPUT_CLASS)


class ArticleAuthorChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, user):
        full_name = user.get_full_name().strip()
        return f"{full_name} (@{user.get_username()})" if full_name else user.get_username()


class ArticleForm(DashboardStyledModelForm):
    author = ArticleAuthorChoiceField(queryset=get_user_model().objects.none(), required=False)

    class Meta:
        model = Article
        fields = ("title", "title_fa", "slug", "summary", "summary_fa", "content", "content_fa", "cover_image", "status", "published_at", "author", "category", "tags", "related_products", "meta_title", "meta_title_fa", "meta_description", "meta_description_fa")
        widgets = {
            "content": CKEditor5Widget(config_name="article_ltr", attrs={"dir": "ltr"}),
            "content_fa": CKEditor5Widget(config_name="article_rtl", attrs={"dir": "rtl"}),
            "summary": forms.Textarea(attrs={"rows": 4}),
            "summary_fa": forms.Textarea(attrs={"rows": 4, "dir": "rtl"}),
            "published_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "tags": forms.SelectMultiple(attrs={"class": SELECT_CLASS, "size": 5}),
            "related_products": forms.SelectMultiple(attrs={"class": SELECT_CLASS, "size": 7}),
            "meta_description": forms.Textarea(attrs={"rows": 3}),
            "meta_description_fa": forms.Textarea(attrs={"rows": 3, "dir": "rtl"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {
            "title": _("Title"), "title_fa": _("Persian title"), "slug": _("Slug"),
            "summary": _("Summary"), "summary_fa": _("Persian summary"),
            "content": _("Content"), "content_fa": _("Persian content"),
            "cover_image": _("Cover image"), "status": _("Status"),
            "published_at": _("Published at"), "author": _("Author"),
            "category": _("Category"), "tags": _("Tags"),
            "related_products": _("Related products"), "meta_title": _("Meta title"),
            "meta_title_fa": _("Persian meta title"),
            "meta_description": _("Meta description"),
            "meta_description_fa": _("Persian meta description"),
        }
        for name, label in labels.items():
            self.fields[name].label = label
        User = get_user_model()
        self.fields["author"].queryset = User.objects.with_perm(
            "blog.change_article", is_active=True, include_superusers=True,
        ).order_by("first_name", "last_name", "username", "pk")
        if not self.instance.pk and user:
            self.initial.setdefault("author", user)
        # Publication is a separate business capability.  Omitting these
        # inputs is presentation only; views also reject a crafted transition.
        if user is not None and not user.has_perm("blog.publish_article"):
            self.fields.pop("status")
            self.fields.pop("published_at")
        self.fields["content_fa"].widget.attrs["dir"] = "rtl"
        self.fields["title_fa"].widget.attrs["dir"] = "rtl"

    def clean_slug(self):
        slug = self.cleaned_data["slug"].strip()
        if not slug:
            slug = slugify(self.cleaned_data.get("title", ""))
        if not slug:
            raise forms.ValidationError("Provide a URL slug (Persian-only titles need a transliterated slug).")
        return slug

    def clean_cover_image(self):
        image = self.cleaned_data.get("cover_image")
        return validate_raster_image(image) if image else image


class SitePageForm(DashboardStyledModelForm):
    class Meta:
        model = SitePage
        fields = (
            "title", "title_fa", "summary", "summary_fa", "content", "content_fa",
            "is_published", "meta_title", "meta_title_fa", "meta_description",
            "meta_description_fa",
        )
        widgets = {
            "content": CKEditor5Widget(config_name="article_ltr", attrs={"dir": "ltr"}),
            "content_fa": CKEditor5Widget(config_name="article_rtl", attrs={"dir": "rtl"}),
            "summary": forms.Textarea(attrs={"rows": 4}),
            "summary_fa": forms.Textarea(attrs={"rows": 4, "dir": "rtl"}),
            "meta_description": forms.Textarea(attrs={"rows": 3}),
            "meta_description_fa": forms.Textarea(attrs={"rows": 3, "dir": "rtl"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {
            "title": _("Title"), "title_fa": _("Persian title"),
            "summary": _("Summary"), "summary_fa": _("Persian summary"),
            "content": _("Content"), "content_fa": _("Persian content"),
            "is_published": _("Published"), "meta_title": _("Meta title"),
            "meta_title_fa": _("Persian meta title"),
            "meta_description": _("Meta description"),
            "meta_description_fa": _("Persian meta description"),
        }
        for name, label in labels.items():
            self.fields[name].label = label
        for name in ("title_fa", "summary_fa", "meta_title_fa", "meta_description_fa"):
            self.fields[name].widget.attrs["dir"] = "rtl"

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("is_published"):
            required = {
                "title": _("Enter the English title before publishing."),
                "title_fa": _("Enter the Persian title before publishing."),
                "content": _("Enter the English content before publishing."),
                "content_fa": _("Enter the Persian content before publishing."),
            }
            for field_name, message in required.items():
                if not (cleaned_data.get(field_name) or "").strip():
                    self.add_error(field_name, message)
        return cleaned_data


class CategoryForm(DashboardStyledModelForm):
    class Meta:
        model = BlogCategory
        fields = ("name", "name_fa", "slug", "description", "description_fa", "icon", "color")


class TagForm(DashboardStyledModelForm):
    class Meta:
        model = BlogTag
        fields = ("name", "name_fa", "slug")


class ArticleAttachmentForm(DashboardStyledModelForm):
    class Meta:
        model = ArticleAttachment
        fields = ("file", "title", "title_fa", "description", "description_fa", "sort_order")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "description_fa": forms.Textarea(attrs={"rows": 2, "dir": "rtl"}),
            "title_fa": forms.TextInput(attrs={"dir": "rtl"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {
            "file": _("File"), "title": _("Title"), "title_fa": _("Persian title"),
            "description": _("Description"), "description_fa": _("Persian description"),
            "sort_order": _("Sort order"),
        }
        for name, label in labels.items():
            self.fields[name].label = label


ArticleAttachmentFormSet = inlineformset_factory(
    Article, ArticleAttachment,
    form=ArticleAttachmentForm,
    extra=1, can_delete=True,
)
