from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404

from blog.models import Article, ArticleStatus, BlogCategory, BlogTag, SitePage, SitePageKey


def article_queryset():
    return Article.objects.select_related("author", "category").prefetch_related("tags", "related_products", "attachments").annotate(attachment_count=Count("attachments", distinct=True))


def get_articles(*, search="", status="", category="", author="", ordering="newest"):
    qs = article_queryset()
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(title_fa__icontains=search) | Q(summary__icontains=search) | Q(summary_fa__icontains=search) | Q(author__username__icontains=search) | Q(category__name__icontains=search) | Q(category__name_fa__icontains=search) | Q(tags__name__icontains=search) | Q(tags__name_fa__icontains=search)).distinct()
    if status == "scheduled":
        from django.utils import timezone
        qs = qs.filter(status=ArticleStatus.PUBLISHED, published_at__gt=timezone.now())
    elif status in dict(ArticleStatus.choices):
        qs = qs.filter(status=status)
    if category:
        qs = qs.filter(category_id=category)
    if author:
        qs = qs.filter(author_id=author)
    return qs.order_by({"oldest": "created_at", "updated": "-updated_at", "title": "title"}.get(ordering, "-created_at"))


def get_article(pk):
    return get_object_or_404(article_queryset(), pk=pk)


def get_site_pages():
    canonical_order = Case(
        *[When(key=key, then=Value(position)) for position, key in enumerate(SitePageKey.values)],
        output_field=IntegerField(),
    )
    return SitePage.objects.order_by(canonical_order)


def get_site_page(pk):
    return get_object_or_404(SitePage, pk=pk, key__in=SitePageKey.values)


def category_queryset():
    return BlogCategory.objects.annotate(article_count=Count("articles")).order_by("name")


def tag_queryset():
    return BlogTag.objects.annotate(article_count=Count("articles")).order_by("name")
