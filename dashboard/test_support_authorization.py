from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from support.models import Ticket, TicketCategory, TicketPriority


class SupportAuthorizationTests(TestCase):
    def setUp(self):
        call_command("seed_dashboard_roles", verbosity=0)
        User = get_user_model(); self.customer = User.objects.create_user("customer", password="secret")
        self.other = User.objects.create_user("other", password="secret")
        self.manager = User.objects.create_user("support-manager", password="secret")
        self.manager.groups.add(Group.objects.get(name="Support Manager"))
        self.reply_agent = User.objects.create_user("reply-agent", password="secret")
        for code in ("view_ticket", "view_ticketreply", "add_ticketreply"):
            self.reply_agent.user_permissions.add(Permission.objects.get(content_type__app_label="support", codename=code))
        self.legacy = User.objects.create_user("legacy", password="secret", is_staff=True)
        self.order_manager = User.objects.create_user("order-manager", password="secret")
        self.order_manager.groups.add(Group.objects.get(name="Order Manager"))
        self.owner = User.objects.create_superuser("owner", "owner@example.com", "secret")
        self.category = TicketCategory.objects.create(name="Delivery", slug="delivery")
        self.ticket = Ticket.objects.create(user=self.customer, category=self.category, subject="Delayed", message="Where is it?", priority=TicketPriority.MEDIUM)

    def test_support_manager_non_staff_can_use_support_but_not_orders(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("dashboard:support:inbox")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dashboard:support:detail", args=[self.ticket.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("dashboard:support:action", args=[self.ticket.pk, "reply"]), {"message": "We are checking."}).status_code, 302)
        self.assertEqual(self.client.post(reverse("dashboard:support:action", args=[self.ticket.pk, "note"]), {"message": "Internal trace"}).status_code, 302)
        self.assertEqual(self.client.get(reverse("dashboard:all_orders")).status_code, 403)

    def test_ticket_action_permissions_are_action_specific(self):
        self.client.force_login(self.reply_agent)
        self.assertEqual(self.client.post(reverse("dashboard:support:action", args=[self.ticket.pk, "reply"]), {"message": "Reply"}).status_code, 302)
        for action, data in (("note", {"message": "No"}), ("assign_me", {}), ("resolve", {}), ("controls", {"assignee": "", "priority": "urgent", "category": self.category.pk, "workflow": ""})):
            self.assertEqual(self.client.post(reverse("dashboard:support:action", args=[self.ticket.pk, action]), data).status_code, 403)
        self.ticket.refresh_from_db(); self.assertEqual(self.ticket.priority, TicketPriority.MEDIUM)

    def test_legacy_staff_and_unrelated_role_are_denied(self):
        for user in (self.legacy, self.order_manager):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("dashboard:support:inbox")).status_code, 403)

    def test_public_attachment_uses_ticket_ownership_or_support_permissions(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from support.models import TicketAttachment
        attachment = TicketAttachment.objects.create(ticket=self.ticket, uploaded_by=self.customer, file=SimpleUploadedFile("note.pdf", b"%PDF-1.4"), original_filename="note.pdf")
        url = reverse("support:attachment_download", args=[attachment.pk])
        self.client.force_login(self.customer); self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.other); self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.legacy); self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.manager); self.assertEqual(self.client.get(url).status_code, 200)
