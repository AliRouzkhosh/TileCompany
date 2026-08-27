##################### blogs/admin.py ##################
from django.contrib import admin

from .models import (
    Article,
    BlogCategory,
    BlogTag,
    Comment,
    ArticleAttachment,
)


@admin.register(BlogCategory)
class BlogCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "name_fa", "slug")
    search_fields = ("name", "name_fa", "description", "description_fa")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(BlogTag)
class BlogTagAdmin(admin.ModelAdmin):
    list_display = ("name", "name_fa", "slug")
    search_fields = ("name", "name_fa")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "author",
        "category",
        "status",
        "reading_time",
        "reading_time_fa",
        "published_at",
        "updated_at",
    )

    list_filter = (
        "status",
        "category",
        "author",
        "published_at",
        "updated_at",
    )
    list_per_page = 25
    save_on_top = True
    search_fields = (
        "title",
        "title_fa",
        "summary",
        "summary_fa",
        "content",
        "content_fa",
        "meta_title",
        "meta_title_fa",
        "meta_description",
        "meta_description_fa",
        "author__username",
        "author__first_name",
        "author__last_name",
        "category__name",
        "category__name_fa",
        "tags__name",
        "tags__name_fa",
    )

    ordering = (
        "-published_at",
        "-created_at",
    )

    prepopulated_fields = {
        "slug": ("title",),
    }

    autocomplete_fields = (
        "author",
        "category",
    )

    filter_horizontal = (
        "tags",
        "related_products",
    )

    readonly_fields = (
        "reading_time",
        "reading_time_fa",
        "created_at",
        "updated_at",
    )

    date_hierarchy = "published_at"

    list_editable = (
        "status",
    )
    list_select_related = (
        "author",
        "category",
    )

    fieldsets = (
        (
            "Content",
            {
                "fields": (
                    "title",
                    "title_fa",
                    "slug",
                    "summary",
                    "summary_fa",
                    "content",
                    "content_fa",
                    "cover_image",
                )
            },
        ),
        (
            "Organization",
            {
                "fields": (
                    "author",
                    "category",
                    "tags",
                    "related_products",
                )
            },
        ),
        (
            "Publishing",
            {
                "fields": (
                    "status",
                    "published_at",
                )
            },
        ),
        (
            "SEO",
            {
                "classes": ("collapse",),
                "fields": (
                    "meta_title",
                    "meta_title_fa",
                    "meta_description",
                    "meta_description_fa",
                ),
            },
        ),
        (
            "Metadata",
            {
                "classes": ("collapse",),
                "fields": (
                    "reading_time",
                    "reading_time_fa",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = (
        "article",
        "parent",
        "user",
        "name",
        "is_approved",
        "created_at",
    )
    list_select_related = (
        "article",
        "user",
        "parent",
    )
    list_filter = (
        "is_approved",
        "created_at",
    )

    search_fields = (
        "name",
        "email",
        "body",
        "article__title",
    )

    autocomplete_fields = (
        "article",
        "user",
        "parent",
    )

    list_editable = (
        "is_approved",
    )

    readonly_fields = (
        "created_at",
    )

    ordering = ("-created_at",)


@admin.register(ArticleAttachment)
class ArticleAttachmentAdmin(admin.ModelAdmin):
    list_display = ("title", "article", "sort_order", "uploaded_at")
    list_select_related = ("article",)
    search_fields = ("title", "title_fa", "article__title")
