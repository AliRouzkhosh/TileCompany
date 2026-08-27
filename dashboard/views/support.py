from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from dashboard.forms_support import HelpArticleForm, InternalNoteForm, StaffReplyForm, TicketCategoryForm, TicketControlForm
from dashboard.permissions import dashboard_permission_required
from dashboard.selectors.support import get_support_queue_counts, get_support_tickets, get_ticket_categories_with_counts, get_ticket_for_dashboard, response_state, support_agents
from support.models import HelpArticle, TicketCategory
from support.services import add_internal_note, assign_ticket, assign_ticket_to_self, change_ticket_category, change_ticket_priority, close_ticket, create_ticket_reply, reopen_ticket, resolve_ticket, unassign_ticket


@dashboard_permission_required("support.view_ticket")
def inbox(request):
    filters = {key: request.GET.get(key, "").strip() for key in ("queue", "search", "status", "priority", "category", "assignee", "sla")}
    filters["user"] = request.user
    page_obj = Paginator(get_support_tickets(**filters), 25).get_page(request.GET.get("page"))
    for ticket in page_obj: ticket.response_state, ticket.response_started_at = response_state(ticket)
    params = request.GET.copy(); params.pop("page", None)
    return render(request, "dashboard/support/inbox.html", {"tickets": page_obj, "filters": filters, "counts": get_support_queue_counts(request.user), "categories": TicketCategory.objects.all(), "agents": support_agents(), "query_string": params.urlencode()})


@dashboard_permission_required("support.view_ticket", "support.view_ticketreply")
def ticket_detail(request, pk):
    ticket = get_ticket_for_dashboard(pk); ticket.response_state, ticket.response_started_at = response_state(ticket)
    can_manage = request.user.has_perm("support.change_ticket")
    return render(request, "dashboard/support/detail.html", {
        "ticket": ticket,
        "reply_form": StaffReplyForm() if request.user.has_perm("support.add_ticketreply") else None,
        "note_form": InternalNoteForm() if request.user.has_perm("support.add_ticketnote") else None,
        "control_form": TicketControlForm(ticket=ticket, agents=support_agents()) if can_manage else None,
        "can_view_notes": request.user.has_perm("support.view_ticketnote"),
        "can_view_attachments": request.user.has_perm("support.view_ticketattachment"),
        "can_manage_ticket": can_manage,
    })


@login_required
def ticket_action(request, pk, action):
    if request.method != "POST": return HttpResponseNotAllowed(["POST"])
    action_permissions = {
        "reply": ("support.view_ticket", "support.add_ticketreply"), "note": ("support.view_ticket", "support.add_ticketnote"),
        "assign_me": ("support.view_ticket", "support.change_ticket"), "unassign": ("support.view_ticket", "support.change_ticket"),
        "resolve": ("support.view_ticket", "support.change_ticket"), "reopen": ("support.view_ticket", "support.change_ticket"),
        "close": ("support.view_ticket", "support.change_ticket"), "controls": ("support.view_ticket", "support.change_ticket"),
    }
    permissions = action_permissions.get(action)
    if not permissions or not request.user.has_perms(permissions): raise PermissionDenied
    if action == "reply" and request.FILES and not request.user.has_perm("support.add_ticketattachment"): raise PermissionDenied
    ticket = get_ticket_for_dashboard(pk)
    if action == "reply":
        form = StaffReplyForm(request.POST, request.FILES)
        if form.is_valid():
            try: create_ticket_reply(ticket=ticket, author=request.user, message=form.cleaned_data["message"], is_staff_reply=True, attachments=form.cleaned_data["attachments"]); messages.success(request, _("Reply sent."))
            except ValidationError as exc: messages.error(request, exc)
    elif action == "note":
        form = InternalNoteForm(request.POST)
        if form.is_valid(): add_internal_note(ticket=ticket, author=request.user, message=form.cleaned_data["message"]); messages.success(request, _("Internal note added."))
    elif action == "assign_me": assign_ticket_to_self(ticket=ticket, actor=request.user)
    elif action == "unassign": unassign_ticket(ticket=ticket, actor=request.user)
    elif action == "resolve": resolve_ticket(ticket=ticket, actor=request.user)
    elif action == "reopen": reopen_ticket(ticket=ticket, actor=request.user)
    elif action == "close": close_ticket(ticket=ticket, actor=request.user)
    else:
        form = TicketControlForm(request.POST, ticket=ticket, agents=support_agents())
        if form.is_valid():
            assignee = form.cleaned_data["assignee"]; (unassign_ticket if assignee is None else assign_ticket)(ticket=ticket, actor=request.user, **({} if assignee is None else {"assignee": assignee})); change_ticket_priority(ticket=ticket, actor=request.user, priority=form.cleaned_data["priority"]); change_ticket_category(ticket=ticket, actor=request.user, category=form.cleaned_data["category"])
            workflow = form.cleaned_data["workflow"]
            if workflow == "resolve": resolve_ticket(ticket=ticket, actor=request.user)
            elif workflow == "reopen": reopen_ticket(ticket=ticket, actor=request.user)
            elif workflow == "close": close_ticket(ticket=ticket, actor=request.user)
            messages.success(request, _("Ticket changes saved."))
    return redirect("dashboard:support:detail", pk=pk)


@dashboard_permission_required("support.view_ticketcategory")
def category_list(request): return render(request, "dashboard/support/categories.html", {"categories": get_ticket_categories_with_counts()})

@login_required
def category_edit(request, pk=None):
    if not request.user.has_perm("support.change_ticketcategory" if pk else "support.add_ticketcategory"): raise PermissionDenied
    obj = get_object_or_404(TicketCategory, pk=pk) if pk else None; form = TicketCategoryForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid(): form.save(); return redirect("dashboard:support:categories")
    return render(request, "dashboard/support/form.html", {"form": form, "title": _("Ticket category")})

@dashboard_permission_required("support.delete_ticketcategory")
def category_delete(request, pk):
    obj = get_object_or_404(TicketCategory, pk=pk)
    if request.method == "POST":
        if obj.tickets.exists(): messages.error(request, ngettext("This category is used by %(count)d ticket and cannot be deleted.", "This category is used by %(count)d tickets and cannot be deleted.", obj.tickets.count()) % {"count": obj.tickets.count()})
        else: obj.delete(); messages.success(request, _("Category deleted."))
        return redirect("dashboard:support:categories")
    return render(request, "dashboard/support/confirm_delete.html", {"object": obj})

@dashboard_permission_required("support.view_helparticle")
def article_list(request): return render(request, "dashboard/support/articles.html", {"articles": HelpArticle.objects.select_related("category")})

@login_required
def article_edit(request, pk=None):
    if not request.user.has_perm("support.change_helparticle" if pk else "support.add_helparticle"): raise PermissionDenied
    obj = get_object_or_404(HelpArticle, pk=pk) if pk else None; form = HelpArticleForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        article = form.save(commit=False); article.published_at = timezone.now() if article.is_published and not article.published_at else None if not article.is_published else article.published_at; article.save(); messages.success(request, _("Help article saved.")); return redirect("dashboard:support:article_edit", pk=article.pk)
    return render(request, "dashboard/support/article_editor.html", {"form": form, "article": obj})

@dashboard_permission_required("support.delete_helparticle")
def article_delete(request, pk):
    obj = get_object_or_404(HelpArticle, pk=pk)
    if request.method == "POST": obj.delete(); return redirect("dashboard:support:articles")
    return render(request, "dashboard/support/confirm_delete.html", {"object": obj})
