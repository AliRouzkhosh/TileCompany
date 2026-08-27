from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from .models import Ticket, TicketActivity, TicketAttachment, TicketNote, TicketReply, TicketStatus
from notifications.models import Notification, NotificationEvent
from notifications.services import create_notification, reverse_notification_target
from TileCompany.uploads import safe_upload_filename

def _activity(ticket, actor, event, message=""): return TicketActivity.objects.create(ticket=ticket, actor=actor, event_type=event, message=message)
def _support_actor(user, *, requires_ticket_management=False):
    permission = "support.change_ticket" if requires_ticket_management else "support.add_ticketreply"
    if not user or not user.has_perm(permission):
        raise ValidationError("Only authorized support users can perform this action.")


def _notify_ticket_owner(ticket, *, event_type, payload):
    create_notification(
        user=ticket.user,
        category=Notification.Category.SUPPORT,
        event_type=event_type,
        payload=payload,
        target_url=reverse_notification_target(
            "support:ticket_detail",
            kwargs={"ticket_id": ticket.id},
        ),
    )

@transaction.atomic
def create_ticket(*, user, subject, category, message, order=None, attachments=(), created_by=None):
    if order and order.user_id != user.id: raise PermissionDenied("This order does not belong to you.")
    ticket = Ticket.objects.create(user=user, category=category, order=order, subject=subject, message=message)
    actor = created_by or user
    _activity(ticket, actor, TicketActivity.Event.CREATED)
    _add_attachments(ticket, actor, attachments)
    return ticket

def _add_attachments(ticket, user, files, reply=None):
    for file in files or ():
        TicketAttachment.objects.create(ticket=ticket, reply=reply, uploaded_by=user, file=file, original_filename=safe_upload_filename(file.name))
        _activity(ticket, user, TicketActivity.Event.ATTACHMENT)

@transaction.atomic
def create_ticket_reply(*, ticket, author, message, is_staff_reply=False, attachments=(), notify_owner=True):
    if ticket.status == TicketStatus.CLOSED: raise ValidationError(_("Closed tickets cannot receive replies."))
    if is_staff_reply: _support_actor(author)
    elif ticket.user_id != author.id: raise PermissionDenied(_("You cannot reply to this ticket."))
    prior = ticket.status; now = timezone.now()
    reply = TicketReply.objects.create(ticket=ticket, author=author, message=message, is_staff_reply=is_staff_reply)
    fields = ["last_reply_at", "status"]
    ticket.last_reply_at = now
    if is_staff_reply:
        ticket.last_staff_reply_at = now; ticket.status = TicketStatus.WAITING_CUSTOMER; fields.append("last_staff_reply_at")
    else:
        ticket.last_customer_reply_at = now; ticket.status = TicketStatus.WAITING_SUPPORT; fields.append("last_customer_reply_at")
        if prior == TicketStatus.RESOLVED: _activity(ticket, author, TicketActivity.Event.REOPENED, "Customer reply reopened ticket")
    ticket.save(update_fields=fields)
    _add_attachments(ticket, author, attachments, reply)
    if is_staff_reply and notify_owner:
        _notify_ticket_owner(
            ticket,
            event_type=NotificationEvent.SUPPORT_REPLY,
            payload={"ticket_subject": ticket.subject},
        )
    return reply

@transaction.atomic
def add_internal_note(*, ticket, author, message):
    _support_actor(author, requires_ticket_management=True); return TicketNote.objects.create(ticket=ticket, author=author, message=message)
@transaction.atomic
def assign_ticket(*, ticket, actor, assignee):
    _support_actor(actor, requires_ticket_management=True); _support_actor(assignee, requires_ticket_management=True)
    if ticket.assigned_to_id == assignee.id: return ticket
    old = ticket.assigned_to; ticket.assigned_to = assignee; ticket.assigned_at = timezone.now(); ticket.save(update_fields=["assigned_to","assigned_at"])
    _activity(ticket, actor, TicketActivity.Event.ASSIGNED, f"Assigned to {assignee.get_username()}"); return ticket
def assign_ticket_to_self(*, ticket, actor): return assign_ticket(ticket=ticket, actor=actor, assignee=actor)
@transaction.atomic
def unassign_ticket(*, ticket, actor):
    _support_actor(actor, requires_ticket_management=True)
    if not ticket.assigned_to_id: return ticket
    ticket.assigned_to = None; ticket.assigned_at = None; ticket.save(update_fields=["assigned_to","assigned_at"]); _activity(ticket, actor, TicketActivity.Event.UNASSIGNED); return ticket
def _change(ticket, actor, field, value, event):
    _support_actor(actor, requires_ticket_management=True); old = getattr(ticket, field)
    if old != value: setattr(ticket, field, value); ticket.save(update_fields=[field]); _activity(ticket, actor, event, f"{old} → {value}")
    return ticket
def change_ticket_priority(*, ticket, actor, priority): return _change(ticket, actor, "priority", priority, TicketActivity.Event.PRIORITY)
def change_ticket_category(*, ticket, actor, category): return _change(ticket, actor, "category", category, TicketActivity.Event.CATEGORY)
@transaction.atomic
def resolve_ticket(*, ticket, actor):
    _support_actor(actor, requires_ticket_management=True)
    if ticket.status != TicketStatus.CLOSED and ticket.status != TicketStatus.RESOLVED:
        ticket.status=TicketStatus.RESOLVED; ticket.resolved_at=timezone.now(); ticket.save(update_fields=["status","resolved_at"]); _activity(ticket,actor,TicketActivity.Event.RESOLVED)
        _notify_ticket_owner(ticket, event_type=NotificationEvent.SUPPORT_STATUS_CHANGED, payload={"ticket_subject": ticket.subject, "status": TicketStatus.RESOLVED})
    return ticket
@transaction.atomic
def reopen_ticket(*, ticket, actor):
    _support_actor(actor, requires_ticket_management=True)
    if ticket.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED): ticket.status=TicketStatus.WAITING_SUPPORT; ticket.save(update_fields=["status"]); _activity(ticket,actor,TicketActivity.Event.REOPENED)
    return ticket
@transaction.atomic
def close_ticket(*, ticket, actor):
    _support_actor(actor, requires_ticket_management=True)
    if ticket.status != TicketStatus.CLOSED:
        ticket.status=TicketStatus.CLOSED; ticket.closed_at=timezone.now(); ticket.save(update_fields=["status","closed_at"]); _activity(ticket,actor,TicketActivity.Event.CLOSED)
        _notify_ticket_owner(ticket, event_type=NotificationEvent.SUPPORT_STATUS_CHANGED, payload={"ticket_subject": ticket.subject, "status": TicketStatus.CLOSED})
    return ticket
