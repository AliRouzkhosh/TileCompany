################ support/views.py ######################
# support/views.py

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from TileCompany.uploads import safe_upload_filename
from django.utils.translation import gettext as _, gettext_lazy, pgettext
from TileCompany.htmx import is_htmx_target, render_page_or_fragment

from .forms import TicketCreateForm, TicketReplyForm
from .selectors import (
    get_ticket_replies,
    get_user_ticket,
    get_user_tickets,
    get_user_ticket_status_counts,
    get_published_help_articles,
)
from .services import (
    create_ticket,
    create_ticket_reply,
)
from .help_presentation import HELP_TOPICS, topic_for_slug


SUPPORT_CONTACT = {
    "support_phone": "+49 69 1234 5678",
    "support_email": "support@yourtilestore.com",
}


TICKET_FILTERS = (
    ("all", gettext_lazy("All")),
    ("open", gettext_lazy("Open")),
    ("waiting_customer", gettext_lazy("Waiting for you")),
    ("waiting_support", gettext_lazy("Waiting for support")),
    ("resolved", gettext_lazy("Resolved")),
    ("closed", gettext_lazy("Closed")),
)

TICKET_EMPTY_STATES = {
    "all": (gettext_lazy("No tickets yet"), gettext_lazy("Need help? Create a support ticket and we’ll keep the conversation here.")),
    "open": (gettext_lazy("No open tickets"), gettext_lazy("Create a ticket if you need help with an order or your account.")),
    "waiting_customer": (gettext_lazy("No tickets waiting for your reply"), gettext_lazy("You have no support conversations that need information from you.")),
    "waiting_support": (gettext_lazy("No tickets waiting for support"), gettext_lazy("Support is not currently reviewing one of your tickets.")),
    "resolved": (gettext_lazy("No resolved tickets"), gettext_lazy("Resolved support conversations will appear here.")),
    "closed": (gettext_lazy("No closed tickets"), gettext_lazy("Closed support conversations will appear here.")),
}


def support_home(request):
    articles = list(get_published_help_articles(request.GET.get("q", ""))[:6])
    from .models import TicketCategory
    published_category_slugs = set(
        TicketCategory.objects.filter(help_articles__is_published=True)
        .values_list("slug", flat=True)
        .distinct()
    )
    return render(
        request,
        "support/support_home.html",
        {
            "articles": articles,
            "has_help_articles": bool(articles),
            "help_topics": tuple(topic for topic in HELP_TOPICS if published_category_slugs.intersection(topic[3])),
            "breadcrumbs": [{"label": _("Support"), "url": None}],
        },
    )


@login_required
def ticket_list(request):
    status = request.GET.get("status", "all")
    sort = request.GET.get("sort", "latest")
    tickets = get_user_tickets(request.user, status=status, sort=sort)
    page_obj = Paginator(tickets, 12).get_page(request.GET.get("page"))
    status_counts = get_user_ticket_status_counts(request.user)
    selected_status = status if status in dict(TICKET_FILTERS) else "all"
    selected_sort = sort if sort in ("latest", "oldest") else "latest"
    empty_heading, empty_description = TICKET_EMPTY_STATES[selected_status]

    context = {
            "tickets": page_obj,
            "page_obj": page_obj,
            "status_counts": status_counts,
            "ticket_filter_options": tuple({"value": value, "label": label, "count": status_counts[value]} for value, label in TICKET_FILTERS),
            "selected_status": selected_status,
            "selected_sort": selected_sort,
            "empty_heading": empty_heading,
            "empty_description": empty_description,
            "breadcrumbs": [
                {"label": pgettext("account breadcrumb", "My Account"), "url": reverse("accounts:overview")},
                {"label": _("Support"), "url": reverse("support:support_home")},
                {"label": _("My Support Tickets"), "url": None},
            ],
        }
    if is_htmx_target(request, "account-workspace"):
        context["account_content_template"] = "support/components/ticket_list_workspace.html"
        return render(request, "accounts/components/account_workspace.html", context)
    if is_htmx_target(request, "ticket-list-workspace"):
        return render(request, "support/components/ticket_list_workspace.html", context)
    return render(request, "support/ticket_list.html", context)


@login_required
def ticket_create(request):
    if request.method == "POST":
        form = TicketCreateForm(request.POST, request.FILES, user=request.user)

        if form.is_valid():
            create_ticket(
                user=request.user,
                category=form.cleaned_data["category"],
                subject=form.cleaned_data["subject"],
                message=form.cleaned_data["message"],
                order=form.cleaned_data["order"],
                attachments=form.cleaned_data["attachments"],
            )

            messages.success(
                request,
                _("Your support ticket has been submitted successfully."),
            )

            return redirect("support:ticket_list")

    else:
        form = TicketCreateForm(user=request.user)

    return render(
        request,
        "support/ticket_create.html",
        {
            "form": form,
            "breadcrumbs": [
                {"label": pgettext("account breadcrumb", "My Account"), "url": reverse("accounts:overview")},
                {"label": _("Support"), "url": reverse("support:support_home")},
                {"label": _("My Support Tickets"), "url": reverse("support:ticket_list")},
                {"label": _("Create ticket"), "url": None},
            ],
        },
    )


@login_required
def ticket_detail(request, ticket_id):
    ticket = get_user_ticket(
        user=request.user,
        ticket_id=ticket_id,
    )

    if request.method == "POST":
        form = TicketReplyForm(request.POST, request.FILES)

        if form.is_valid():
            try:
                create_ticket_reply(ticket=ticket, author=request.user, message=form.cleaned_data["message"], attachments=form.cleaned_data["attachments"])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, _("Your reply has been added."))
                return redirect("support:ticket_detail", ticket_id=ticket.pk)

    else:
        form = TicketReplyForm()

    replies = get_ticket_replies(ticket)

    return render(
        request,
        "support/ticket_detail.html",
        {
            "ticket": ticket,
            "replies": replies,
            "form": form,
            "breadcrumbs": [
                {"label": pgettext("account breadcrumb", "My Account"), "url": reverse("accounts:overview")},
                {"label": _("Support"), "url": reverse("support:support_home")},
                {"label": _("My Support Tickets"), "url": reverse("support:ticket_list")},
                {"label": ticket.subject, "url": None},
            ],
        },
    )

def help_list(request):
    from .models import TicketCategory
    topic_key = request.GET.get("topic", "")
    topic = next((item for item in HELP_TOPICS if item[0] == topic_key), None)
    category_slug = request.GET.get("category", "")
    selected_category = TicketCategory.objects.filter(slug=category_slug).first() if category_slug else None
    if category_slug and selected_category is None:
        category_slug = ""
    articles = get_published_help_articles(request.GET.get("q", ""), category_slug)
    if topic:
        articles = articles.filter(category__slug__in=topic[3])
    return render_page_or_fragment(request, "support/help_list.html", "support/components/help_discovery.html", {
        "articles": articles,
        "help_topics": HELP_TOPICS,
        "selected_topic": topic,
        "selected_category": selected_category,
        "breadcrumbs": [
            {"label": _("Support"), "url": reverse("support:support_home")},
            {"label": _("Help Center"), "url": None},
        ],
    })

def help_detail(request, slug):
    from .models import HelpArticle
    article = get_object_or_404(HelpArticle.objects.select_related("category"), slug=slug, is_published=True)
    from django.utils.translation import get_language
    return render(request, "support/help_detail.html", {
        "article": article,
        "article_topic": topic_for_slug(article.category.slug),
        "related_articles": get_published_help_articles(category=article.category.slug).exclude(pk=article.pk)[:3],
        "breadcrumbs": [
            {"label": _("Support"), "url": reverse("support:support_home")},
            {"label": article.category.name_fa if (get_language() or "").startswith("fa") and article.category.name_fa else article.category.name, "url": reverse("support:help_list") + f"?category={article.category.slug}"},
            {"label": article.title_fa if (get_language() or "").startswith("fa") and article.title_fa else article.title, "url": None},
        ],
    })

@login_required
def attachment_download(request, attachment_id):
    from .models import TicketAttachment
    attachment = get_object_or_404(TicketAttachment.objects.select_related("ticket"), pk=attachment_id)
    is_support_actor = request.user.has_perms(("support.view_ticket", "support.view_ticketattachment"))
    if attachment.ticket.user_id != request.user.id and not is_support_actor:
        raise Http404
    response = FileResponse(attachment.file.open("rb"), as_attachment=True, filename=safe_upload_filename(attachment.original_filename or attachment.file.name.rsplit("/", 1)[-1]))
    response["Cache-Control"] = "private, no-store"
    return response
