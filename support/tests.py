from django.test import TestCase

# Create your tests here.
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.datastructures import MultiValueDict
from pathlib import Path
from orders.models import Order
from .models import HelpArticle, Ticket, TicketCategory, TicketStatus
from .services import add_internal_note, assign_ticket, close_ticket, create_ticket, create_ticket_reply, resolve_ticket
from .forms import TicketReplyForm


class SupportWorkflowTests(TestCase):
    def setUp(self):
        call_command("seed_dashboard_roles", verbosity=0)
        self.user = get_user_model().objects.create_user("customer", "customer@example.com", "pass")
        self.staff = get_user_model().objects.create_user("agent", "agent@example.com", "pass")
        self.staff.groups.add(Group.objects.get(name="Support Manager"))
        self.category = TicketCategory.objects.create(name="Delivery")
        self.ticket = create_ticket(user=self.user, category=self.category, subject="Delivery delayed", message="My delivery has not arrived yet.")

    def test_create_ticket_uses_model_default_priority(self):
        self.assertEqual(self.ticket.priority, "medium")

    def test_customer_reply_reopens_resolved_ticket(self):
        resolve_ticket(ticket=self.ticket, actor=self.staff)
        create_ticket_reply(ticket=self.ticket, author=self.user, message="It is still not resolved.")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, TicketStatus.WAITING_SUPPORT)
        self.assertIsNotNone(self.ticket.last_customer_reply_at)

    def test_staff_reply_and_note_preserve_privacy_and_status(self):
        create_ticket_reply(ticket=self.ticket, author=self.staff, message="We are checking this now.", is_staff_reply=True)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, TicketStatus.WAITING_CUSTOMER)
        add_internal_note(ticket=self.ticket, author=self.staff, message="Check courier trace.")
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, TicketStatus.WAITING_CUSTOMER)
        self.assertEqual(self.ticket.notes.count(), 1)

    def test_customer_cannot_be_assigned(self):
        with self.assertRaises(ValidationError):
            assign_ticket(ticket=self.ticket, actor=self.staff, assignee=self.user)

    def test_closed_ticket_blocks_customer_reply(self):
        close_ticket(ticket=self.ticket, actor=self.staff)
        with self.assertRaises(ValidationError):
            create_ticket_reply(ticket=self.ticket, author=self.user, message="Please reopen this.")

    def test_multi_file_reply_form_accepts_allowed_uploads(self):
        upload = SimpleUploadedFile("evidence.pdf", b"%PDF-1.4 support evidence", content_type="application/pdf")
        form = TicketReplyForm({"message": "Please see the attached invoice."}, MultiValueDict({"attachments": [upload]}))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["attachments"], [upload])

    def test_attachment_form_rejects_client_mime_spoofing(self):
        upload = SimpleUploadedFile("evidence.jpg", b"not an image", content_type="image/jpeg")
        form = TicketReplyForm({"message": "Please see the attached invoice."}, MultiValueDict({"attachments": [upload]}))
        self.assertFalse(form.is_valid())
        self.assertIn("valid JPEG, PNG, or WEBP image", form.errors["attachments"][0])

    def test_persian_customer_support_pages_render_localized_ui(self):
        self.category.name_fa = "ارسال"
        self.category.save(update_fields=["name_fa"])
        self.client.force_login(self.user)

        pages = (
            "/fa/support/",
            "/fa/support/tickets/",
            "/fa/support/tickets/new/",
            f"/fa/support/tickets/{self.ticket.pk}/",
        )
        for url in pages:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

        self.assertContains(self.client.get("/fa/support/"), "چگونه می‌توانیم کمک کنیم؟")
        self.assertContains(self.client.get("/fa/support/tickets/"), "تیکت‌های پشتیبانی من")
        self.assertContains(self.client.get("/fa/support/tickets/new/"), "ایجاد تیکت پشتیبانی")
        self.assertContains(self.client.get(f"/fa/support/tickets/{self.ticket.pk}/"), "اطلاعات تیکت")
        self.assertContains(self.client.get(f"/fa/support/tickets/{self.ticket.pk}/"), "ارسال")
        self.assertContains(self.client.get("/en/support/"), "How can we help?")

    def test_ticket_text_html_payload_is_rendered_as_plain_text(self):
        self.ticket.message = '<img src=x onerror="alert(1)"><script>alert(1)</script>'
        self.ticket.save(update_fields=["message"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("support:ticket_detail", args=[self.ticket.pk]))
        self.assertContains(response, "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", html=False)
        self.assertNotContains(response, '<img src=x onerror="alert(1)">', html=False)


class SupportHtmxNavigationContractTests(TestCase):
    def test_create_ticket_actions_opt_out_of_the_ticket_workspace_target(self):
        root = Path(__file__).resolve().parent / "templates" / "support" / "components"
        empty = (root / "support_empty.html").read_text(encoding="utf-8")
        workspace = (root / "ticket_list_workspace.html").read_text(encoding="utf-8")

        self.assertIn('href="{% url \'support:ticket_create\' %}" hx-boost="false"', empty)
        self.assertIn('href="{% url \'support:ticket_create\' %}" hx-boost="false"', workspace)
        self.assertIn("data-pagination-scroll-target", workspace)


class CustomerTicketDiscoveryTests(TestCase):
    def setUp(self):
        call_command("seed_dashboard_roles", verbosity=0)
        self.user = get_user_model().objects.create_user("customer", "customer@example.com", "pass")
        self.other_user = get_user_model().objects.create_user("other", "other@example.com", "pass")
        self.staff = get_user_model().objects.create_user("agent", "agent@example.com", "pass")
        self.staff.groups.add(Group.objects.get(name="Support Manager"))
        self.category = TicketCategory.objects.create(name="Delivery", name_fa="ارسال")
        self.open_ticket = create_ticket(user=self.user, category=self.category, subject="Open delivery", message="My delivery has not arrived yet.")
        self.waiting_customer = create_ticket(user=self.user, category=self.category, subject="Need information", message="Please tell me what you need from me.")
        create_ticket_reply(ticket=self.waiting_customer, author=self.staff, message="Please send the invoice.", is_staff_reply=True)
        self.resolved = create_ticket(user=self.user, category=self.category, subject="Resolved delivery", message="The delivery question is now resolved.")
        resolve_ticket(ticket=self.resolved, actor=self.staff)
        create_ticket(user=self.other_user, category=self.category, subject="Private ticket", message="This ticket must not appear for customer.")
        self.client.force_login(self.user)

    def test_filters_counts_sort_and_pagination_contract(self):
        url = "/en/support/tickets/"
        response = self.client.get(url, {"status": "waiting_customer", "sort": "oldest"})
        self.assertContains(response, "Waiting for you")
        self.assertContains(response, self.waiting_customer.subject)
        self.assertNotContains(response, self.open_ticket.subject)
        self.assertEqual(response.context["status_counts"]["all"], 3)
        self.assertEqual(response.context["selected_sort"], "oldest")
        invalid = self.client.get(url, {"status": "forged", "sort": "forged"})
        self.assertEqual(invalid.context["selected_status"], "all")
        self.assertEqual(invalid.context["selected_sort"], "latest")

    def test_thread_uses_safe_customer_and_support_avatar_contract(self):
        response = self.client.get(reverse("support:ticket_detail", args=[self.waiting_customer.pk]))
        self.assertContains(response, "support-thread-avatar--customer")
        self.assertContains(response, "support-thread-avatar--support")
        self.assertContains(response, "data-lucide=\"headset\"")
        self.assertNotContains(response, self.staff.username)

    def test_support_home_exposes_real_actions_without_placeholder_contact(self):
        response = self.client.get(reverse("support:support_home"))
        self.assertContains(response, reverse("support:ticket_create"))
        self.assertContains(response, reverse("support:ticket_list"))
        self.assertNotContains(response, "support@yourtilestore.com")

    def test_ticket_workspace_uses_one_account_shell_and_localized_filters(self):
        fa_url = lambda url: url.replace("/en/", "/fa/", 1)
        list_response = self.client.get(fa_url(reverse("support:ticket_list")))
        self.assertContains(list_response, 'data-support-account-workspace')
        self.assertContains(list_response, 'data-account-navigation')
        self.assertEqual(list_response.content.count(b'data-account-nav-desktop'), 1)
        for label in ("همه", "باز", "در انتظار پاسخ شما", "در انتظار پشتیبانی", "حل‌شده", "بسته‌شده", "آخرین فعالیت", "قدیمی‌ترین فعالیت"):
            self.assertContains(list_response, label)
        for label in ("Waiting for you", "Waiting for support", "Latest activity", "Oldest activity"):
            self.assertNotContains(list_response, label)

        create_response = self.client.get(fa_url(reverse("support:ticket_create")))
        detail_response = self.client.get(fa_url(reverse("support:ticket_detail", args=[self.waiting_customer.pk])))
        for response in (create_response, detail_response):
            self.assertContains(response, 'data-support-account-workspace')
            self.assertContains(response, 'data-account-navigation')

    def test_ticket_list_distinguishes_account_navigation_and_inner_filter_targets(self):
        url = reverse("support:ticket_list")
        account = self.client.get(url, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="account-workspace")
        self.assertContains(account, 'id="account-workspace"')
        self.assertContains(account, 'id="ticket-list-workspace"')
        self.assertNotContains(account, "<html", html=False)

        inner = self.client.get(url + "?status=open", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="ticket-list-workspace")
        self.assertContains(inner, 'id="ticket-list-workspace"')
        self.assertNotContains(inner, 'id="account-workspace"')
        self.assertNotContains(inner, "<html", html=False)


class HelpCenterLocalizationTests(TestCase):
    def setUp(self):
        self.category = TicketCategory.objects.create(
            name="Orders & Delivery", name_fa="سفارش و ارسال", slug="orders-delivery"
        )
        self.article = HelpArticle.objects.create(
            category=self.category,
            title="How do I track my order?",
            title_fa="چگونه سفارشم را پیگیری کنم؟",
            slug="track-an-order",
            summary="Open My Orders to review the recorded status for an order.",
            summary_fa="برای دیدن وضعیت ثبت‌شدهٔ سفارش، بخش سفارش‌های من را باز کنید.",
            content="Open My Orders and select the relevant order.",
            content_fa="بخش سفارش‌های من را باز کنید و سفارش موردنظر را انتخاب کنید.",
            is_published=True,
        )
        HelpArticle.objects.create(
            category=self.category,
            title="What do order statuses mean?",
            title_fa="وضعیت‌های سفارش چه معنایی دارند؟",
            slug="order-statuses",
            summary="The order detail page shows the recorded order status.",
            summary_fa="صفحهٔ جزئیات سفارش، وضعیت ثبت‌شدهٔ سفارش را نشان می‌دهد.",
            content="Review the detail page for the current order status.",
            content_fa="برای دیدن وضعیت فعلی، صفحهٔ جزئیات سفارش را بررسی کنید.",
            is_published=True,
        )

    def test_persian_help_chrome_uses_compiled_translations(self):
        home = self.client.get("/fa/support/")
        listing = self.client.get("/fa/support/help/")
        detail = self.client.get(f"/fa/support/help/{self.article.slug}/")

        self.assertContains(home, "موضوع‌های پرطرفدار راهنما")
        self.assertContains(listing, "مرکز راهنما")
        self.assertContains(listing, "سفارش و ارسال")
        self.assertContains(detail, "راهنماهای مرتبط")
        self.assertContains(detail, "هنوز به کمک نیاز دارید؟")
        self.assertContains(detail, "یک تیکت پشتیبانی ایجاد کنید تا گفت‌وگو در حساب کاربری شما بماند.")
        for response in (home, listing, detail):
            for leaked in ("Popular topics", "Related guides", "Still need help?", "Create a support ticket and keep the conversation with your account."):
                self.assertNotContains(response, leaked)

    def test_help_topic_and_search_state_are_server_rendered(self):
        response = self.client.get("/en/support/help/", {"topic": "orders", "q": "track"})
        self.assertContains(response, self.article.title)
        self.assertContains(response, 'name="topic" value="orders"', html=False)
        self.assertEqual(self.client.get("/en/support/help/", {"topic": "unknown"}).status_code, 200)
