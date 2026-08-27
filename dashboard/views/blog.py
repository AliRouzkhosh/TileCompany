from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from blog.context_processors import FOOTER_SITE_PAGE_ROUTES
from blog.models import Article, ArticleStatus, BlogCategory, BlogTag
from blog.services import create_article, delete_article, publish_article, unpublish_article, update_article, update_site_page
from dashboard.forms_blog import ArticleAttachmentFormSet, ArticleForm, CategoryForm, SitePageForm, TagForm
from dashboard.permissions import dashboard_permission_required
from dashboard.selectors.blog import category_queryset, get_article, get_articles, get_site_page, get_site_pages, tag_queryset


def _site_page_identity_label(page):
    return FOOTER_SITE_PAGE_ROUTES[page.key][1]


def _site_page_preview_context(page):
    from django.utils.translation import get_language

    is_persian = (get_language() or "").startswith("fa")
    return {
        "site_page": page,
        "page_title": page.title_fa if is_persian else page.title,
        "page_summary": page.summary_fa if is_persian else page.summary,
        "page_content": page.content_fa if is_persian else page.content,
        "page_meta_title": (
            (page.meta_title_fa or page.title_fa)
            if is_persian else (page.meta_title or page.title)
        ),
        "page_meta_description": page.meta_description_fa if is_persian else page.meta_description,
        "breadcrumbs": [{"label": _("Preview"), "url": None}],
        "preview_mode": True,
    }

@dashboard_permission_required("blog.view_article")
def article_catalog(request):
    filters = {key: request.GET.get(key, "").strip() for key in ("search", "status", "category", "author", "ordering")}
    page_obj = Paginator(get_articles(**filters), 20).get_page(request.GET.get("page"))
    now = timezone.now()
    stats = (
        {"label": _("All"), "count": Article.objects.count()},
        {"label": _("Draft"), "count": Article.objects.filter(status=ArticleStatus.DRAFT).count()},
        {"label": _("Published"), "count": Article.objects.filter(status=ArticleStatus.PUBLISHED, published_at__lte=now).count()},
        {"label": _("Scheduled"), "count": Article.objects.filter(status=ArticleStatus.PUBLISHED, published_at__gt=now).count()},
    )
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "dashboard/blog/catalog.html", {"articles": page_obj.object_list, "page_obj": page_obj, "filters": filters, "categories": category_queryset(), "authors": get_user_model().objects.with_perm("blog.change_article", is_active=True, include_superusers=True), "stats": stats, "query_string": params.urlencode()})

def _article_form(request, article=None):
    form = ArticleForm(request.POST or None, request.FILES or None, instance=article, user=request.user)
    formset = ArticleAttachmentFormSet(request.POST or None, request.FILES or None, instance=article)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        saved = update_article(article=article, form=form, attachments=formset) if article else create_article(form=form, attachments=formset)
        messages.success(request, _("Article saved successfully."))
        return redirect("dashboard:blog:edit", pk=saved.pk)
    return render(request, "dashboard/blog/editor.html", {"form": form, "formset": formset, "article": article})

@dashboard_permission_required("blog.add_article")
def article_create(request): return _article_form(request)
@dashboard_permission_required("blog.change_article")
def article_edit(request, pk): return _article_form(request, get_article(pk))
@dashboard_permission_required("blog.view_article")
def article_preview(request, pk):
    article = get_article(pk)
    return render(request, "blog/article_detail.html", {"article": article, "comments": [], "related_products": article.related_products.all(), "related_articles": [], "categories": category_queryset(), "latest_articles": [], "popular_tags": tag_queryset(), "preview_mode": True})
@dashboard_permission_required("blog.change_article", "blog.publish_article")
def article_publish(request, pk):
    if request.method != "POST": return HttpResponseNotAllowed(["POST"])
    publish_article(get_article(pk)); messages.success(request, _("Article published or scheduled."))
    return redirect("dashboard:blog:edit", pk=pk)
@dashboard_permission_required("blog.change_article", "blog.publish_article")
def article_unpublish(request, pk):
    if request.method != "POST": return HttpResponseNotAllowed(["POST"])
    unpublish_article(get_article(pk)); messages.success(request, _("Article returned to draft."))
    return redirect("dashboard:blog:edit", pk=pk)
@dashboard_permission_required("blog.delete_article")
def article_delete(request, pk):
    article = get_article(pk)
    if request.method == "POST": delete_article(article); messages.success(request, _("Article permanently deleted.")); return redirect("dashboard:blog:catalog")
    return render(request, "dashboard/blog/confirm_delete.html", {"object": article, "kind": "article", "comment_count": article.comments.count()})


@dashboard_permission_required("blog.view_sitepage")
def site_page_list(request):
    pages = list(get_site_pages())
    for page in pages:
        page.identity_label = _site_page_identity_label(page)
    return render(request, "dashboard/blog/site_page_list.html", {"site_pages": pages})


@dashboard_permission_required("blog.change_sitepage")
def site_page_edit(request, pk):
    page = get_site_page(pk)
    form = SitePageForm(request.POST or None, instance=page)
    if request.method == "POST" and form.is_valid():
        page = update_site_page(site_page=page, form=form)
        messages.success(request, _("Page saved successfully."))
        return redirect("dashboard:blog:page_edit", pk=page.pk)
    return render(
        request,
        "dashboard/blog/site_page_editor.html",
        {"form": form, "site_page": page, "identity_label": _site_page_identity_label(page)},
    )


@dashboard_permission_required("blog.view_sitepage")
def site_page_preview(request, pk):
    return render(request, "blog/site_page_detail.html", _site_page_preview_context(get_site_page(pk)))

def _taxonomy(request, model, form_class, pk=None, kind="category"):
    obj = get_object_or_404(model, pk=pk) if pk else None
    form = form_class(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("dashboard:blog:categories" if kind == "category" else "dashboard:blog:tags")
    return render(request, "dashboard/blog/taxonomy_form.html", {"form": form, "kind": kind, "object": obj})
@dashboard_permission_required("blog.view_blogcategory")
def category_list(request): return render(request, "dashboard/blog/taxonomy_list.html", {"items": category_queryset(), "kind": "category"})
@dashboard_permission_required("blog.view_blogtag")
def tag_list(request): return render(request, "dashboard/blog/taxonomy_list.html", {"items": tag_queryset(), "kind": "tag"})
@dashboard_permission_required("blog.add_blogcategory", "blog.change_blogcategory", any_of=True)
def category_edit(request, pk=None):
    if pk and not request.user.has_perm("blog.change_blogcategory"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    if not pk and not request.user.has_perm("blog.add_blogcategory"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return _taxonomy(request, BlogCategory, CategoryForm, pk, "category")
@dashboard_permission_required("blog.add_blogtag", "blog.change_blogtag", any_of=True)
def tag_edit(request, pk=None):
    if pk and not request.user.has_perm("blog.change_blogtag"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    if not pk and not request.user.has_perm("blog.add_blogtag"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return _taxonomy(request, BlogTag, TagForm, pk, "tag")
def _tax_delete(request, model, pk, kind):
    obj = get_object_or_404(model, pk=pk)
    article_count = obj.articles.count()
    if request.method == "POST":
        if article_count:
            messages.error(request, _("This %(kind)s is still linked to %(count)s article(s). Resolve those links before deleting it.") % {"kind": kind, "count": article_count})
            return redirect("dashboard:blog:categories" if kind == "category" else "dashboard:blog:tags")
        obj.delete(); messages.success(request, _("%(kind)s deleted.") % {"kind": kind.title()})
        return redirect("dashboard:blog:categories" if kind == "category" else "dashboard:blog:tags")
    return render(request, "dashboard/blog/confirm_delete.html", {"object": obj, "kind": kind, "article_count": article_count})
@dashboard_permission_required("blog.delete_blogcategory")
def category_delete(request, pk): return _tax_delete(request, BlogCategory, pk, "category")
@dashboard_permission_required("blog.delete_blogtag")
def tag_delete(request, pk): return _tax_delete(request, BlogTag, pk, "tag")
