################3 blogs/selectors.py ######################
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.db.models import Count, Q
from django.utils import timezone
from products.selectors import annotate_product_price_range

from .models import (
    Article,
    ArticleStatus,
    BlogCategory,
    BlogTag,
    Comment,
    SitePage,
    SitePageKey,
)


ARTICLE_SORT_NEWEST = "newest"
ARTICLE_SORT_OLDEST = "oldest"
ARTICLE_SORT_TITLE_ASC = "title-asc"
ARTICLE_SORT_TITLE_DESC = "title-desc"
ARTICLE_SORT_VALUES = {
    ARTICLE_SORT_NEWEST,
    ARTICLE_SORT_OLDEST,
    ARTICLE_SORT_TITLE_ASC,
    ARTICLE_SORT_TITLE_DESC,
}


def get_published_site_page(key):
    """Return one canonical public page without ever exposing a draft."""
    if key not in SitePageKey.values:
        raise Http404
    return get_object_or_404(SitePage, key=key, is_published=True)


def get_published_site_page_keys():
    """One bounded footer lookup for the tiny canonical page set."""
    return SitePage.objects.filter(is_published=True).values_list("key", flat=True)


def get_published_articles():
    return (
        Article.objects
        .filter(status=ArticleStatus.PUBLISHED, published_at__lte=timezone.now())
        .select_related("author", "category")
    )


def order_published_articles(queryset, sort, language):
    """Apply one allowlisted, locale-aware public Article ordering."""
    is_persian = (language or "").lower().replace("_", "-").startswith("fa")
    title_field = "title_fa" if is_persian else "title"
    ordering = {
        ARTICLE_SORT_NEWEST: ("-published_at", "-pk"),
        ARTICLE_SORT_OLDEST: ("published_at", "pk"),
        ARTICLE_SORT_TITLE_ASC: (title_field, "pk"),
        ARTICLE_SORT_TITLE_DESC: (f"-{title_field}", "pk"),
    }
    return queryset.order_by(*ordering.get(sort, ordering[ARTICLE_SORT_NEWEST]))

def get_latest_articles(limit=8):
    return (
        get_published_articles()
        # Storefront editorial cards retain the existing tag-aware payload.
        # The public Articles landing intentionally uses get_published_articles()
        # directly because its simplified cards do not render tags.
        .prefetch_related("tags")
        .order_by("-published_at")[:limit]
    )

def get_blog_categories():
    return (
        BlogCategory.objects.annotate(
            article_count=Count(
                "articles",
                filter=Q(
                    articles__status=ArticleStatus.PUBLISHED,
                    articles__published_at__lte=timezone.now(),
                ),
            )
        )
        .filter(article_count__gt=0)
        .order_by("name")
    )

def get_related_articles(article, limit=3):
    """Deterministic, published-only continuation with same-category priority."""
    queryset = get_published_articles().exclude(pk=article.pk).order_by("-published_at", "-pk")
    related = []
    if article.category_id:
        related = list(queryset.filter(category_id=article.category_id)[:limit])
    if len(related) < limit:
        related.extend(
            queryset.exclude(pk__in=[item.pk for item in related])[: limit - len(related)]
        )
    return related

def get_article_by_slug(slug):
    return get_object_or_404(
        get_published_articles().select_related(
            "author",
            "category",
        ).prefetch_related(
            "tags",
            "related_products",
            "attachments",
        ),
        slug=slug,
    )


def get_article_comments(article, viewer=None):
    """Build approved comments plus the authenticated viewer's own pending work."""
    visibility = Q(is_approved=True)
    if getattr(viewer, "is_authenticated", False):
        visibility |= Q(user=viewer)
    comments = list(
        article.comments.filter(visibility)
        .select_related("user")
        .order_by("created_at", "pk")
    )
    children = {}
    for comment in comments:
        children.setdefault(comment.parent_id, []).append(comment)
    for comment in comments:
        comment.editorial_replies = children.get(comment.pk, [])
    return children.get(None, [])


def get_related_products(article, currency, limit=12):
    """Return article-linked products with prices for the active storefront currency."""
    return (
        annotate_product_price_range(
            article.related_products.filter(is_active=True).prefetch_related("materials"),
            currency,
        )[:limit]
    )
def get_reply_parent(article, comment_id):
    """
    Return an approved parent comment for an article or None.
    """
    if not comment_id:
        return None

    return (
        Comment.objects
        .filter(
            pk=comment_id,
            article=article,
            is_approved=True,
        )
        .first()
    )


def get_comment(pk):
    return get_object_or_404(Comment, pk=pk)


def get_user_owned_comment(*, pk, user):
    """Return a comment only when its authenticated author owns it."""
    return get_object_or_404(Comment, pk=pk, user=user)


def can_user_delete_comment(user, comment):
    return (
        user.is_authenticated
        and (
            comment.user == user
            or user.has_perm("blog.moderate_article_comments")
            or user.is_superuser
        )
    )

def get_popular_tags(limit=12):
    return BlogTag.objects.annotate(article_count=Count("articles", filter=Q(articles__status=ArticleStatus.PUBLISHED, articles__published_at__lte=timezone.now()))).filter(article_count__gt=0).order_by("-article_count", "name")[:limit]


def get_blog_tags():
    """Tags that can actually return a published Article result."""
    return (
        BlogTag.objects.annotate(
            article_count=Count(
                "articles",
                filter=Q(
                    articles__status=ArticleStatus.PUBLISHED,
                    articles__published_at__lte=timezone.now(),
                ),
            )
        )
        .filter(article_count__gt=0)
        .order_by("name")
    )
