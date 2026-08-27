############## blogs/views.py ##################

from django.contrib import messages
from django.http import HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from TileCompany.htmx import is_htmx, render_page_or_fragment
from django.urls import reverse
from django.core.paginator import Paginator
from orders.currency import get_request_currency
from django.utils.translation import gettext as _
from django.utils.translation import get_language
from products.catalog_presentation import prepare_product_card_presentations

from .forms import CommentForm
from .models import Comment
from .selectors import (
    ARTICLE_SORT_NEWEST,
    ARTICLE_SORT_VALUES,
    can_user_delete_comment,
    get_article_by_slug,
    get_article_comments,
    get_comment,
    get_user_owned_comment,
    get_published_articles,
    get_published_site_page,
    get_related_products,
    get_reply_parent,
    get_related_articles,
    get_blog_categories,
    get_blog_tags,
    order_published_articles,
)
from .services import (
    create_comment,
    delete_comment,
    update_comment,
    user_can_comment,
    get_comment_cooldown_remaining,
)


def site_page_detail(request, key):
    page = get_published_site_page(key)
    is_persian = (get_language() or "").startswith("fa")
    return render(
        request,
        "blog/site_page_detail.html",
        {
            "site_page": page,
            "page_title": page.title_fa if is_persian else page.title,
            "page_summary": page.summary_fa if is_persian else page.summary,
            "page_content": page.content_fa if is_persian else page.content,
            "page_meta_title": (
                (page.meta_title_fa or page.title_fa)
                if is_persian
                else (page.meta_title or page.title)
            ),
            "page_meta_description": (
                page.meta_description_fa if is_persian else page.meta_description
            ),
            "breadcrumbs": [{
                "label": page.title_fa if is_persian else page.title,
                "url": None,
            }],
        },
    )


def article_list(request):
    articles = get_published_articles()
    categories = get_blog_categories()
    tags = get_blog_tags()

    selected_category = request.GET.get("category", "").strip()
    selected_tag = request.GET.get("tag", "").strip()
    query = request.GET.get("q", "").strip()
    selected_sort = request.GET.get("sort", ARTICLE_SORT_NEWEST).strip()
    if selected_sort not in ARTICLE_SORT_VALUES:
        selected_sort = ARTICLE_SORT_NEWEST

    selected_category_object = next(
        (category for category in categories if category.slug == selected_category), None
    )
    if selected_category and selected_category_object is None:
        selected_category = ""
    selected_tag_object = next((tag for tag in tags if tag.slug == selected_tag), None)
    if selected_tag and selected_tag_object is None:
        selected_tag = ""

    if selected_category:
        articles = articles.filter(category__slug=selected_category)
    if selected_tag:
        articles = articles.filter(tags__slug=selected_tag)
    if query:
        from django.db.models import Q
        articles = articles.filter(Q(title__icontains=query) | Q(title_fa__icontains=query) | Q(summary__icontains=query) | Q(summary_fa__icontains=query) | Q(content__icontains=query) | Q(content_fa__icontains=query) | Q(category__name__icontains=query) | Q(category__name_fa__icontains=query) | Q(tags__name__icontains=query) | Q(tags__name_fa__icontains=query)).distinct()

    articles = order_published_articles(articles, selected_sort, get_language())
    paginator = Paginator(articles, 9)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    query_params["sort"] = selected_sort
    breadcrumbs = [{"label": _("Articles"), "url": None}]
    if selected_category_object:
        category_label = (
            selected_category_object.name_fa
            if (get_language() or "").startswith("fa") and selected_category_object.name_fa
            else selected_category_object.name
        )
        breadcrumbs = [
            {"label": _("Articles"), "url": reverse("blog:article_list")},
            {"label": category_label, "url": None},
        ]

    return render_page_or_fragment(
        request,
        "blog/article_list.html",
        "blog/components/article_discovery.html",
        {
            "articles": page_obj,
            "page_obj": page_obj,
            "categories": categories,
            "tags": tags,
            "selected_category": selected_category,
            "selected_category_object": selected_category_object,
            "selected_tag": selected_tag,
            "selected_tag_object": selected_tag_object,
            "query": query,
            "selected_sort": selected_sort,
            "total_articles": paginator.count,
            "query_string": query_params.urlencode(),
            "breadcrumbs": breadcrumbs,
        },
    )


def _comment_section(request, article, *, form=None, editing_comment_id=None, edit_form=None, reply_to=None, cooldown_seconds=0):
    return render(request, "blog/components/comment_section.html", {
        "article": article,
        "comments": get_article_comments(article, viewer=request.user),
        "comment_form": form or CommentForm(),
        "editing_comment_id": editing_comment_id,
        "edit_form": edit_form,
        "reply_to": reply_to or "",
        "cooldown_seconds": cooldown_seconds,
    })


def article_detail(request, slug):
    article = get_article_by_slug(slug)
    comments = get_article_comments(article, viewer=request.user)
    storefront_currency = get_request_currency(request)
    related_products = list(get_related_products(article, storefront_currency))
    prepare_product_card_presentations(
        related_products, language=get_language(), currency=storefront_currency
    )
    related_articles = get_related_articles(article)
    initial = {}

    if request.user.is_authenticated:
        initial = {
            "name": request.user.get_full_name() or request.user.username,
            "email": request.user.email,
        }

    if request.method == "POST":

        if not request.user.is_authenticated:
            if is_htmx(request):
                return HttpResponseForbidden(_("Please log in to comment."))
            messages.error(request, _("Please log in to comment."))
            return redirect(f"/accounts/login/?next={request.path}")

        form = CommentForm(request.POST, initial=initial)

        if not form.is_valid() and is_htmx(request):
            return _comment_section(request, article, form=form)

        if form.is_valid():

            if not user_can_comment(request.user):
                remaining = get_comment_cooldown_remaining(request.user)
                if is_htmx(request):
                    return _comment_section(request, article, form=form, reply_to=request.POST.get("reply_to"), cooldown_seconds=remaining)
                messages.error(
                    request,
                    _("Please wait a moment. You can post again in %(seconds)s seconds.") % {"seconds": remaining},
                )
                return redirect(f"{article.get_absolute_url()}#comments")

            parent = None
            reply_to = request.POST.get("reply_to")
            parent = get_reply_parent(article, reply_to)

            if reply_to and parent is None:
                if is_htmx(request):
                    form.add_error(None, _("That comment is no longer available for replies."))
                    return _comment_section(request, article, form=form)
                messages.error(request, _("That comment is no longer available for replies."))
                return redirect(f"{article.get_absolute_url()}#comments")

            success, result = create_comment(
                article=article,
                user=request.user,
                form=form,
                parent=parent,
            )

            if not success:
                if is_htmx(request):
                    form.add_error(None, result)
                    return _comment_section(request, article, form=form)
                messages.error(request, result)
                return redirect(f"{article.get_absolute_url()}#comments")

            messages.success(
                request,
                _("Thanks! Your comment was submitted and awaits approval."),
            )

            if is_htmx(request):
                return _comment_section(request, article)

            return redirect(f"{article.get_absolute_url()}#comments")

    else:
        form = CommentForm(initial=initial)

    return render(
        request,
        "blog/article_detail.html",
        {
            "article": article,
            "comments": comments,
            "comment_form": form,
            "related_products": related_products,
            "storefront_currency": storefront_currency,
            "related_articles": related_articles,
            "breadcrumbs": [
                {"label": _("Articles"), "url": reverse("blog:article_list")},
                {"label": article.title_fa if (get_language() or "").startswith("fa") and article.title_fa else article.title, "url": None},
            ],
            "editing_comment_id": request.GET.get("edit_comment", ""),
        },
    )


@login_required
def comment_edit(request, pk):
    comment = get_user_owned_comment(pk=pk, user=request.user)

    if request.method == "POST":
        form = CommentForm(request.POST, instance=comment)
        if form.is_valid():
            was_approved = comment.is_approved
            update_comment(comment=comment, form=form)
            if was_approved:
                messages.success(request, _("Your edited comment now requires moderation again."))
            else:
                messages.success(request, _("Your comment has been updated and remains awaiting moderation."))
            if is_htmx(request):
                return _comment_section(request, comment.article)
            return redirect(f"{comment.article.get_absolute_url()}#comment-{comment.pk}")
    else:
        return redirect(f"{comment.article.get_absolute_url()}?edit_comment={comment.pk}#comment-{comment.pk}")

    if is_htmx(request):
        return _comment_section(
            request,
            comment.article,
            editing_comment_id=comment.pk,
            edit_form=form,
        )
    return redirect(f"{comment.article.get_absolute_url()}?edit_comment={comment.pk}#comment-{comment.pk}")


@require_POST
def comment_delete(request, pk):

    comment = get_comment(pk)

    if not can_user_delete_comment(request.user, comment):
        return HttpResponseForbidden(
            _("You are not allowed to delete this comment.")
        )

    article_url = comment.article.get_absolute_url()

    delete_comment(comment)

    messages.success(request, _("Comment deleted."))

    if is_htmx(request):
        return _comment_section(request, comment.article)

    return redirect(f"{article_url}#comments")
