############### blogs/services.py ############
from math import ceil

from django.utils import timezone
from django.db import transaction
from django.utils.translation import gettext as _

from .models import ArticleStatus, Comment, SitePage


@transaction.atomic
def create_article(*, form, attachments):
    article = form.save()
    attachments.instance = article
    attachments.save()
    return article


@transaction.atomic
def update_article(*, article, form, attachments):
    article = form.save()
    attachments.instance = article
    attachments.save()
    return article


def publish_article(article, published_at=None):
    article.status = ArticleStatus.PUBLISHED
    article.published_at = published_at or article.published_at or timezone.now()
    article.save()
    return article


def unpublish_article(article):
    article.status = ArticleStatus.DRAFT
    article.save()
    return article


@transaction.atomic
def update_site_page(*, site_page, form):
    """Serialize edits to one canonical page and preserve model invariants."""
    locked_page = SitePage.objects.select_for_update().get(pk=site_page.pk)
    for field_name in form._meta.fields:
        setattr(locked_page, field_name, form.cleaned_data[field_name])
    locked_page.save()
    return locked_page


@transaction.atomic
def delete_article(article):
    article.delete()


ARTICLE_DISCUSSION_COOLDOWN_SECONDS = 15


def get_comment_cooldown_remaining(user):
    """Return the server-authoritative remaining create cooldown for one user."""
    latest = Comment.objects.filter(
        user=user,
    ).order_by("-created_at").values_list("created_at", flat=True).first()
    if latest is None:
        return 0
    elapsed = (timezone.now() - latest).total_seconds()
    return max(0, ceil(ARTICLE_DISCUSSION_COOLDOWN_SECONDS - elapsed))


def user_can_comment(user):
    return get_comment_cooldown_remaining(user) == 0


def has_pending_article_comment(user, article):
    """
    Returns True if the user already has a pending
    top-level comment for this article.
    """

    return Comment.objects.filter(
        user=user,
        article=article,
        parent__isnull=True,
        is_approved=False,
    ).exists()


def has_pending_reply(user, parent):
    """
    Returns True if the user already has a pending
    reply to this parent comment.
    """

    return Comment.objects.filter(
        user=user,
        parent=parent,
        is_approved=False,
    ).exists()

def create_comment(
    *,
    article,
    user,
    form,
    parent=None,
):
    """
    Create a new blog comment.

    Returns:
        (True, comment)
        (False, error_message)
    """

    if parent:

        if has_pending_reply(user, parent):
            return (
                False,
                (
                    _("You already have a reply awaiting moderation for this comment.")
                ),
            )

    else:

        if has_pending_article_comment(user, article):
            return (
                False,
                (
                    _("You already have a comment awaiting moderation for this article.")
                ),
            )

    comment = form.save(commit=False)

    comment.article = article
    comment.user = user

    comment.name = (
        user.get_full_name()
        or user.username
    )

    comment.email = user.email or ""

    if parent:
        comment.parent = parent

    comment.save()

    return (
        True,
        comment,
    )


def delete_comment(comment):
    comment.delete()


@transaction.atomic
def update_comment(*, comment, form):
    """Update only the editable text and return it to moderation."""
    comment.body = form.cleaned_data["body"]
    comment.is_approved = False
    comment.edited_at = timezone.now()
    comment.save(update_fields=("body", "is_approved", "updated_at", "edited_at"))
    return comment
