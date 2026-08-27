from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from orders.models import Order

User = get_user_model()


class TicketStatus(models.TextChoices):
    OPEN = "open", "Open"
    WAITING_CUSTOMER = "waiting_customer", "Waiting for Customer"
    WAITING_SUPPORT = "waiting_support", "Waiting for Support"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class TicketPriority(models.TextChoices):
    LOW = "low", _("Low")
    MEDIUM = "medium", _("Medium")
    HIGH = "high", _("High")
    URGENT = "urgent", _("Urgent")


class TicketCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    name_fa = models.CharField(max_length=100, blank=True)
    slug = models.SlugField(unique=True, blank=True)
    description = models.TextField(blank=True)
    description_fa = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text="Lucide icon name (e.g. package, truck, credit-card).")
    class Meta:
        ordering = ("name",)
        verbose_name = "Ticket Category"
        verbose_name_plural = "Ticket Categories"
    def save(self, *args, **kwargs):
        if not self.slug: self.slug = slugify(self.name)
        super().save(*args, **kwargs)
    def __str__(self): return self.name


class Ticket(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tickets")
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets", help_text="Optional order related to this support request.")
    category = models.ForeignKey(TicketCategory, on_delete=models.PROTECT, related_name="tickets")
    assigned_to = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tickets")
    assigned_at = models.DateTimeField(null=True, blank=True)
    subject = models.CharField(max_length=200, help_text="A short summary of the customer's issue.")
    message = models.TextField(help_text="The initial message submitted by the customer.")
    status = models.CharField(max_length=30, choices=TicketStatus.choices, default=TicketStatus.OPEN, db_index=True)
    priority = models.CharField(max_length=20, choices=TicketPriority.choices, default=TicketPriority.MEDIUM, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    last_reply_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_customer_reply_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_staff_reply_at = models.DateTimeField(null=True, blank=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        ordering = ("-updated_at",)
        verbose_name = "Support Ticket"
        verbose_name_plural = "Support Tickets"
    def __str__(self): return f"Ticket #{self.pk} • {self.subject}"


class TicketReply(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="replies")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="ticket_replies")
    message = models.TextField()
    is_staff_reply = models.BooleanField(default=False, help_text="Whether this reply was written by a staff member.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    class Meta:
        ordering = ("created_at",)
        verbose_name = "Ticket Reply"
        verbose_name_plural = "Ticket Replies"


class TicketNote(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="ticket_notes")
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    class Meta: ordering = ("created_at",)


class TicketActivity(models.Model):
    class Event(models.TextChoices):
        CREATED = "created", "Ticket created"; ASSIGNED = "assigned", "Assigned"; UNASSIGNED = "unassigned", "Unassigned"
        STATUS = "status_changed", "Status changed"; PRIORITY = "priority_changed", "Priority changed"; CATEGORY = "category_changed", "Category changed"
        RESOLVED = "resolved", "Resolved"; REOPENED = "reopened", "Reopened"; CLOSED = "closed", "Closed"; ATTACHMENT = "attachment_added", "Attachment added"
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="activities")
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="ticket_activities")
    event_type = models.CharField(max_length=30, choices=Event.choices)
    message = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    class Meta: ordering = ("created_at",)


class TicketAttachment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="attachments")
    reply = models.ForeignKey(TicketReply, on_delete=models.CASCADE, null=True, blank=True, related_name="attachments")
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="ticket_attachments")
    file = models.FileField(upload_to="support/%Y/%m/")
    original_filename = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    def clean(self):
        if self.reply_id and self.reply.ticket_id != self.ticket_id: raise ValidationError("Attachment reply must belong to the ticket.")


class HelpArticle(models.Model):
    category = models.ForeignKey(TicketCategory, on_delete=models.PROTECT, related_name="help_articles")
    title = models.CharField(max_length=200); title_fa = models.CharField(max_length=200, blank=True)
    slug = models.SlugField(unique=True, blank=True)
    summary = models.TextField(blank=True); summary_fa = models.TextField(blank=True)
    content = models.TextField(); content_fa = models.TextField(blank=True)
    is_published = models.BooleanField(default=False, db_index=True)
    is_featured = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True); updated_at = models.DateTimeField(auto_now=True)
    class Meta: ordering = ("-is_featured", "title")
    def save(self, *args, **kwargs):
        if not self.slug: self.slug = slugify(self.title)
        super().save(*args, **kwargs)
    def __str__(self): return self.title
