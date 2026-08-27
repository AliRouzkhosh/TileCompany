from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from dashboard.services import (
    approve_blog_comment,
    approve_refund,
    approve_review,
    approve_review_comment,
    cancel_unpaid_order as staff_cancel_unpaid_order,
    hold_order,
    mark_as_delivered,
    mark_as_shipped,
    process_refund,
    reject_refund,
    reject_review,
    reject_review_comment,
    resume_order,
)
from orders.models import Order
from orders.services import cancel_unpaid_order, confirm_cod_order, mark_order_paid
from products.models import Product
from reviews.models import Review, ReviewComment
from blog.models import Article
from blog.models import Comment as ArticleComment
from support.models import TicketCategory, TicketStatus
from support.services import close_ticket, create_ticket, create_ticket_reply, resolve_ticket

from .models import Notification, NotificationEvent
from .context_processors import notification_header
from .presentation import get_notification_presentation
from .selectors import (
    get_recent_notifications,
    get_unread_notification_count,
    get_unread_notifications,
    get_user_notifications,
)
from .services import (
    create_notification,
    mark_all_notifications_read,
    mark_notification_read,
    reverse_notification_target,
)


class NotificationFoundationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("notification-user", "user@example.com", "password")
        self.other_user = get_user_model().objects.create_user("other-user", "other@example.com", "password")

    def create_order_notification(self, **overrides):
        values = {
            "user": self.user,
            "category": Notification.Category.ORDER,
            "event_type": NotificationEvent.ORDER_SHIPPED,
            "payload": {"order_number": 42},
            "target_url": "/orders/42/",
        }
        values.update(overrides)
        return create_notification(**values)

    def test_creation_defaults_to_unread_and_keeps_structured_data(self):
        notification = self.create_order_notification()

        self.assertEqual(notification.user, self.user)
        self.assertFalse(notification.is_read)
        self.assertIsNone(notification.read_at)
        self.assertEqual(notification.payload, {"order_number": 42})
        self.assertEqual(notification.target_url, "/orders/42/")
        self.assertFalse(hasattr(notification, "title"))
        self.assertFalse(hasattr(notification, "message"))

    def test_event_category_validation_rejects_mismatch(self):
        with self.assertRaises(ValidationError):
            self.create_order_notification(category=Notification.Category.SUPPORT)

    def test_user_selectors_are_scoped_ordered_and_filterable(self):
        first = self.create_order_notification()
        second = create_notification(
            user=self.user,
            category=Notification.Category.REFUND,
            event_type=NotificationEvent.REFUND_APPROVED,
            payload={"order_number": 43},
        )
        create_notification(
            user=self.other_user,
            category=Notification.Category.ORDER,
            event_type=NotificationEvent.ORDER_CONFIRMED,
        )

        self.assertEqual(list(get_user_notifications(self.user)), [second, first])
        self.assertEqual(list(get_user_notifications(self.user, Notification.Category.REFUND)), [second])
        self.assertEqual(get_unread_notification_count(self.user), 2)
        self.assertEqual(list(get_unread_notifications(self.user)), [second, first])
        self.assertEqual(list(get_recent_notifications(self.user, limit=1)), [second])

    def test_mark_one_read_is_idempotent_and_user_scoped(self):
        notification = self.create_order_notification()

        marked = mark_notification_read(notification=notification, user=self.user)
        first_read_at = marked.read_at
        self.assertTrue(marked.is_read)
        self.assertIsNotNone(first_read_at)

        marked_again = mark_notification_read(notification=notification, user=self.user)
        self.assertEqual(marked_again.read_at, first_read_at)

        other_notification = create_notification(
            user=self.other_user,
            category=Notification.Category.ORDER,
            event_type=NotificationEvent.ORDER_CONFIRMED,
        )
        with self.assertRaises(PermissionDenied):
            mark_notification_read(notification=other_notification, user=self.user)

    def test_mark_all_read_only_changes_the_requesting_users_unread_notifications(self):
        unread = self.create_order_notification()
        already_read = self.create_order_notification(payload={"order_number": 43})
        mark_notification_read(notification=already_read, user=self.user)
        other_notification = create_notification(
            user=self.other_user,
            category=Notification.Category.ORDER,
            event_type=NotificationEvent.ORDER_CONFIRMED,
        )

        self.assertEqual(mark_all_notifications_read(user=self.user), 1)
        unread.refresh_from_db()
        already_read.refresh_from_db()
        other_notification.refresh_from_db()
        self.assertTrue(unread.is_read)
        self.assertIsNotNone(unread.read_at)
        self.assertTrue(already_read.is_read)
        self.assertFalse(other_notification.is_read)
        self.assertEqual(Notification.objects.count(), 3)

    def test_presentation_is_localized_at_display_time_and_handles_bad_payloads(self):
        notification = self.create_order_notification()
        with translation.override("en"):
            english = get_notification_presentation(notification)
        with translation.override("fa"):
            persian = get_notification_presentation(notification)

        self.assertEqual(english.title, "Order shipped")
        self.assertEqual(english.message, "Order #42 has been shipped.")
        self.assertNotEqual(persian.title, english.title)
        self.assertEqual(notification.payload, {"order_number": 42})

        malformed = Notification(event_type=NotificationEvent.ORDER_SHIPPED, payload=[])
        self.assertIn("—", get_notification_presentation(malformed).message)
        legacy = Notification(event_type="legacy_event", payload={})
        with translation.override("en"):
            self.assertEqual(get_notification_presentation(legacy).title, "Notification")

    def test_target_urls_must_be_internal_paths(self):
        notification = self.create_order_notification(target_url="/orders/42/?source=notification#details")
        self.assertEqual(notification.target_url, "/orders/42/?source=notification")

        for target_url in ("https://malicious.example/", "//malicious.example/", "javascript:alert(1)"):
            with self.subTest(target_url=target_url):
                with self.assertRaises(ValidationError):
                    self.create_order_notification(target_url=target_url)

    def test_notification_creation_rolls_back_with_the_surrounding_transaction(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.create_order_notification()
                raise RuntimeError("force rollback")
        self.assertEqual(Notification.objects.count(), 0)

    def test_reversed_notification_targets_use_the_canonical_language_independent_path(self):
        with translation.override("fa"):
            target = reverse_notification_target("orders:order_detail", kwargs={"order_id": 42})
        self.assertEqual(target, "/en/orders/my-orders/42/")

    def test_notification_center_distinguishes_full_account_and_inner_htmx_targets(self):
        self.client.force_login(self.user)
        self.create_order_notification()
        url = reverse("notifications:center")

        full = self.client.get(url)
        self.assertContains(full, "<html", html=False)
        self.assertContains(full, 'id="account-workspace"')
        self.assertContains(full, 'id="notification-workspace"')
        self.assertContains(full, 'data-account-navigation')

        account = self.client.get(url, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="account-workspace")
        self.assertEqual(account.status_code, 200)
        self.assertContains(account, 'id="account-workspace"')
        self.assertContains(account, 'id="notification-workspace"')
        self.assertContains(account, 'aria-current="page"')
        self.assertNotContains(account, "<html", html=False)

        inner = self.client.get(url + "?category=order", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="notification-workspace")
        self.assertEqual(inner.status_code, 200)
        self.assertContains(inner, 'id="notification-workspace"')
        self.assertNotContains(inner, 'id="account-workspace"')
        self.assertNotContains(inner, "<html", html=False)


class NotificationCenterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("center-user", "center@example.com", "password")
        self.other_user = get_user_model().objects.create_user("center-other", "other@example.com", "password")
        self.center_url = reverse("notifications:center")

    def create_notification(self, *, user=None, category=Notification.Category.ORDER, event_type=NotificationEvent.ORDER_SHIPPED, **kwargs):
        return create_notification(
            user=user or self.user,
            category=category,
            event_type=event_type,
            payload={"order_number": kwargs.pop("order_number", 42)},
            **kwargs,
        )

    def test_center_requires_authentication_and_scopes_notifications_to_user(self):
        self.create_notification()
        self.create_notification(user=self.other_user)

        self.assertEqual(self.client.get(self.center_url).status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get(self.center_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item.notification.user_id for item in response.context["notification_items"]], [self.user.id])

    def test_center_filters_categories_handles_invalid_values_and_paginates(self):
        refund = self.create_notification(category=Notification.Category.REFUND, event_type=NotificationEvent.REFUND_APPROVED)
        self.create_notification()
        self.client.force_login(self.user)

        response = self.client.get(f"{self.center_url}?category=refund")
        self.assertEqual([item.notification.pk for item in response.context["notification_items"]], [refund.pk])
        invalid_response = self.client.get(f"{self.center_url}?category=invalid")
        self.assertIsNone(invalid_response.context["selected_category"])

        for number in range(21):
            self.create_notification(order_number=number)
        paginated = self.client.get(f"{self.center_url}?category=order&page=2")
        self.assertEqual(paginated.context["page_obj"].number, 2)
        self.assertContains(paginated, "?category=order&page=1")

    def test_header_context_is_auth_aware_and_limits_preview(self):
        home_url = reverse("products:home")
        anonymous = self.client.get(home_url)
        self.assertEqual(anonymous.context["notification_unread_count"], 0)
        self.assertEqual(anonymous.context["notification_previews"], [])
        self.assertNotContains(anonymous, "data-notification-menu")

        notifications = [self.create_notification(order_number=number) for number in range(6)]
        mark_notification_read(notification=notifications[0], user=self.user)
        self.client.force_login(self.user)
        response = self.client.get(home_url)
        self.assertEqual(response.context["notification_unread_count"], 5)
        self.assertEqual(len(response.context["notification_previews"]), 5)
        self.assertContains(response, "data-notification-menu")

    def test_opening_notification_uses_post_marks_read_and_never_redirects_externally(self):
        notification = self.create_notification(target_url="/orders/42/")
        self.client.force_login(self.user)
        response = self.client.post(reverse("notifications:open", args=[notification.pk]))
        self.assertRedirects(response, "/orders/42/", fetch_redirect_response=False)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertIsNotNone(notification.read_at)

        empty_target = self.create_notification(target_url="")
        self.assertRedirects(self.client.post(reverse("notifications:open", args=[empty_target.pk])), self.center_url, fetch_redirect_response=False)
        unsafe_target = self.create_notification(target_url="/orders/43/")
        Notification.objects.filter(pk=unsafe_target.pk).update(target_url="https://malicious.example/")
        self.assertRedirects(self.client.post(reverse("notifications:open", args=[unsafe_target.pk])), self.center_url, fetch_redirect_response=False)

        other_notification = self.create_notification(user=self.other_user)
        self.assertEqual(self.client.post(reverse("notifications:open", args=[other_notification.pk])).status_code, 404)

    def test_notification_open_preserves_anonymous_and_target_object_ownership_guards(self):
        foreign_order = Order.objects.create(
            user=self.other_user, user_order_number=99, full_name="Other", address="1", city="Tehran", phone="123",
            status=Order.Status.CONFIRMED, payment_method=Order.PaymentMethod.COD,
        )
        notification = self.create_notification(
            target_url=reverse_notification_target("orders:order_detail", kwargs={"order_id": foreign_order.id})
        )
        open_url = reverse("notifications:open", args=[notification.pk])

        anonymous = self.client.post(open_url)
        self.assertEqual(anonymous.status_code, 302)
        notification.refresh_from_db()
        self.assertFalse(notification.is_read)

        self.client.force_login(self.user)
        opened = self.client.post(open_url)
        self.assertRedirects(opened, notification.target_url, fetch_redirect_response=False)
        self.assertEqual(self.client.get(opened.url).status_code, 404)

    def test_mark_all_is_post_only_user_scoped_and_preserves_records(self):
        own = self.create_notification()
        other = self.create_notification(user=self.other_user)
        self.client.force_login(self.user)

        self.assertEqual(self.client.get(reverse("notifications:mark_all_read")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("notifications:mark_all_read")), self.center_url, fetch_redirect_response=False)
        own.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(own.is_read)
        self.assertFalse(other.is_read)
        self.assertEqual(Notification.objects.count(), 2)

    def test_center_uses_localized_presentation_without_raw_event_or_payload(self):
        self.create_notification(order_number=901, target_url="/en/orders/my-orders/901/")
        self.client.force_login(self.user)
        with translation.override("en"):
            english = self.client.get(reverse("notifications:center"))
        with translation.override("fa"):
            persian = self.client.get(reverse("notifications:center"))

        self.assertContains(english, "Order shipped")
        self.assertContains(english, '<html lang="en" dir="ltr">')
        self.assertContains(persian, "سفارش ارسال شد")
        self.assertContains(persian, "مشاهدهٔ سفارش")
        self.assertContains(persian, "۱ اعلان خوانده‌نشده")
        self.assertContains(persian, '<html lang="fa" dir="rtl">')
        for english_copy in ("Notifications", "Mark all as read", "View order", "Unread notification"):
            self.assertNotContains(persian, english_copy)
        self.assertNotContains(english, "order_shipped")
        self.assertNotContains(english, '{"order_number"')

    def test_center_uses_account_shell_one_row_system_and_accessible_unread_state(self):
        notification = self.create_notification(target_url="/en/orders/my-orders/42/")
        read_notification = self.create_notification(order_number=41, target_url="/en/orders/my-orders/41/")
        mark_notification_read(notification=read_notification, user=self.user)
        self.client.force_login(self.user)

        with translation.override("en"):
            response = self.client.get(self.center_url)

        self.assertContains(response, 'class="account-shell"')
        self.assertContains(response, 'class="store-container flex-1 py-6 sm:py-8"')
        self.assertContains(response, "data-account-navigation", count=1)
        self.assertContains(response, "data-account-nav-selector-button", count=1)
        self.assertContains(response, 'aria-current="page"')
        self.assertEqual(response.content.count(b"<h1"), 1)
        self.assertContains(response, 'data-notification-state="unread"')
        self.assertContains(response, 'data-notification-state="read"')
        self.assertContains(response, "Unread notification")
        self.assertContains(response, "Read notification")
        self.assertContains(response, 'data-lucide="truck"')
        for emoji in ("📦", "↩️", "⭐", "💬"):
            self.assertNotContains(response, emoji)
        self.assertEqual(notification.user_id, self.user.id)

    def test_current_workflow_state_softens_stale_payment_and_refund_actions(self):
        payment_order = Order.objects.create(
            user=self.user,
            user_order_number=71,
            full_name="Center user",
            address="1",
            city="Tehran",
            phone="123",
            status=Order.Status.PENDING,
            payment_method=Order.PaymentMethod.MOCK,
            is_paid=False,
        )
        payment_notice = create_notification(
            user=self.user,
            category=Notification.Category.ORDER,
            event_type=NotificationEvent.PAYMENT_FAILED,
            payload={"order_id": payment_order.id, "order_number": 71, "payment_state": "failure"},
            target_url=reverse_notification_target("orders:order_detail", kwargs={"order_id": payment_order.id}),
        )

        refund_order = Order.objects.create(
            user=self.user,
            user_order_number=72,
            full_name="Center user",
            address="1",
            city="Tehran",
            phone="123",
            status=Order.Status.DELIVERED,
            payment_method=Order.PaymentMethod.MOCK,
            is_paid=True,
            refund_status=Order.RefundStatus.NEEDS_INFORMATION,
        )
        category = TicketCategory.objects.create(name="Refund center", slug="refund-center")
        ticket = create_ticket(user=self.user, category=category, subject="Refund #72", message="Context", order=refund_order)
        refund_order.refund_support_ticket = ticket
        refund_order.save(update_fields=["refund_support_ticket"])
        ticket.status = TicketStatus.WAITING_CUSTOMER
        ticket.save(update_fields=["status"])
        refund_notice = create_notification(
            user=self.user,
            category=Notification.Category.REFUND,
            event_type=NotificationEvent.REFUND_NEEDS_INFORMATION,
            payload={"order_number": 72, "ticket_id": ticket.id, "ticket_reply_id": 1},
            target_url=reverse_notification_target("support:ticket_detail", kwargs={"ticket_id": ticket.id}),
        )

        self.client.force_login(self.user)
        current = self.client.get(self.center_url)
        displays = {item.notification.pk: item.presentation for item in current.context["notification_items"]}
        self.assertEqual(displays[payment_notice.pk].action_label, "Review and pay")
        self.assertEqual(displays[refund_notice.pk].action_label, "Provide details")

        payment_order.is_paid = True
        payment_order.status = Order.Status.CONFIRMED
        payment_order.save(update_fields=["is_paid", "status"])
        ticket.status = TicketStatus.WAITING_SUPPORT
        ticket.save(update_fields=["status"])
        stale = self.client.get(self.center_url)
        stale_displays = {item.notification.pk: item.presentation for item in stale.context["notification_items"]}
        self.assertEqual(stale_displays[payment_notice.pk].action_label, "View order")
        self.assertEqual(stale_displays[refund_notice.pk].action_label, "View ticket")

    def test_center_query_count_is_bounded_for_one_and_twenty_mixed_notifications(self):
        self.create_notification(target_url="/en/orders/my-orders/42/")
        self.client.force_login(self.user)
        self.client.get(self.center_url)
        with CaptureQueriesContext(connection) as one_queries:
            self.client.get(self.center_url)
        Notification.objects.filter(user=self.user).delete()

        order = Order.objects.create(
            user=self.user, user_order_number=88, full_name="Center user", address="1", city="Tehran", phone="123",
            status=Order.Status.PENDING, payment_method=Order.PaymentMethod.MOCK, is_paid=False,
        )
        category = TicketCategory.objects.create(name="Query support", slug="query-support")
        ticket = create_ticket(user=self.user, category=category, subject="Ticket #6", message="Help")
        for number in range(7):
            create_notification(
                user=self.user, category=Notification.Category.ORDER, event_type=NotificationEvent.PAYMENT_CANCELLED,
                payload={"order_id": order.id, "order_number": 88, "payment_state": "cancelled"},
                target_url=reverse_notification_target("orders:order_detail", kwargs={"order_id": order.id}),
            )
        for number in range(7):
            create_notification(
                user=self.user, category=Notification.Category.REFUND, event_type=NotificationEvent.REFUND_NEEDS_INFORMATION,
                payload={"order_number": 88, "ticket_id": ticket.id, "ticket_reply_id": number},
                target_url=reverse_notification_target("support:ticket_detail", kwargs={"ticket_id": ticket.id}),
            )
        for number in range(6):
            create_notification(
                user=self.user, category=Notification.Category.SUPPORT, event_type=NotificationEvent.SUPPORT_REPLY,
                payload={"ticket_subject": f"Ticket #6 update {number}"},
                target_url=reverse_notification_target("support:ticket_detail", kwargs={"ticket_id": ticket.id}),
            )

        with CaptureQueriesContext(connection) as many_queries:
            response = self.client.get(self.center_url)
        self.assertEqual(len(response.context["notification_items"]), 20)
        self.assertLessEqual(len(many_queries), len(one_queries) + 2)
        # The shared storefront shell now asks the maintained Dashboard
        # capability predicate before deciding whether to show its link.  It
        # performs one Django-permission lookup per request and is independent
        # of notification volume (the relative bound above remains unchanged).
        self.assertLessEqual(len(many_queries), 13)


class NotificationOrderRefundIntegrationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("notification-buyer", password="secret")
        self.staff = get_user_model().objects.create_user("notification-staff", password="secret", is_staff=True)

    def order(self, *, status=Order.Status.CONFIRMED, paid=False, method=Order.PaymentMethod.COD, refund_status=Order.RefundStatus.NONE, requested_stage=""):
        return Order.objects.create(
            user=self.user,
            user_order_number=42,
            full_name="Notification Buyer",
            address="1 Tile Street",
            city="Tehran",
            phone="123",
            status=status,
            is_paid=paid,
            payment_method=method,
            refund_status=refund_status,
            refund_requested_stage=requested_stage,
        )

    def assert_notification(self, order, *, category, event_type):
        notification = Notification.objects.get(user=self.user, event_type=event_type)
        self.assertEqual(notification.category, category)
        self.assertEqual(notification.payload, {"order_number": order.user_order_number})
        self.assertEqual(
            notification.target_url,
            reverse_notification_target("orders:order_detail", kwargs={"order_id": order.id}),
        )
        return notification

    def test_fulfillment_transitions_create_one_notification_each_and_invalid_actions_create_none(self):
        order = self.order()

        self.assertTrue(hold_order(order, self.staff))
        self.assert_notification(order, category=Notification.Category.ORDER, event_type=NotificationEvent.ORDER_ON_HOLD)
        self.assertTrue(resume_order(order, self.staff))
        self.assert_notification(order, category=Notification.Category.ORDER, event_type=NotificationEvent.ORDER_RESUMED)
        self.assertTrue(mark_as_shipped(order, self.staff))
        self.assert_notification(order, category=Notification.Category.ORDER, event_type=NotificationEvent.ORDER_SHIPPED)
        self.assertFalse(order.is_paid)
        self.assertTrue(mark_as_delivered(order, self.staff))
        self.assert_notification(order, category=Notification.Category.ORDER, event_type=NotificationEvent.ORDER_DELIVERED)
        order.refresh_from_db()
        self.assertTrue(order.is_paid)
        self.assertEqual(order.status, Order.Status.DELIVERED)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 4)

        invalid = self.order(status=Order.Status.PENDING)
        self.assertFalse(mark_as_shipped(invalid, self.staff))
        self.assertFalse(hold_order(invalid, self.staff))
        self.assertFalse(resume_order(invalid, self.staff))
        self.assertFalse(mark_as_delivered(invalid, self.staff))
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 4)

    def test_refund_transitions_notify_once_without_refund_driven_cancellation_duplicate(self):
        approved = self.order(
            paid=True,
            refund_status=Order.RefundStatus.REQUESTED,
            requested_stage=Order.Status.CONFIRMED,
        )
        self.assertTrue(approve_refund(approved, self.staff, "Approved"))
        self.assert_notification(approved, category=Notification.Category.REFUND, event_type=NotificationEvent.REFUND_APPROVED)
        self.assertFalse(approve_refund(approved, self.staff, "Again"))
        self.assertFalse(Notification.objects.filter(event_type=NotificationEvent.ORDER_CANCELLED).exists())
        self.assertTrue(process_refund(approved, self.staff))
        self.assert_notification(approved, category=Notification.Category.REFUND, event_type=NotificationEvent.REFUND_PROCESSED)
        self.assertFalse(process_refund(approved, self.staff))

        rejected = self.order(
            paid=True,
            refund_status=Order.RefundStatus.REQUESTED,
            requested_stage=Order.Status.CONFIRMED,
        )
        self.assertTrue(reject_refund(rejected, self.staff, "Rejected"))
        self.assert_notification(rejected, category=Notification.Category.REFUND, event_type=NotificationEvent.REFUND_REJECTED)
        self.assertFalse(reject_refund(rejected, self.staff, "Again"))
        self.assertEqual(Notification.objects.filter(event_type=NotificationEvent.REFUND_APPROVED).count(), 1)
        self.assertEqual(Notification.objects.filter(event_type=NotificationEvent.REFUND_REJECTED).count(), 1)
        self.assertEqual(Notification.objects.filter(event_type=NotificationEvent.REFUND_PROCESSED).count(), 1)

    def test_confirmation_and_customer_cancellation_are_quiet_but_staff_and_stale_cancellations_notify(self):
        online = self.order(status=Order.Status.PENDING, method=Order.PaymentMethod.MOCK)
        mark_order_paid(online, "payment-ref", "Development gateway")
        cod = self.order(status=Order.Status.PENDING)
        confirm_cod_order(cod)
        self.assertEqual(Notification.objects.count(), 0)

        customer_cancelled = self.order()
        self.assertTrue(cancel_unpaid_order(order=customer_cancelled, actor=self.user))
        self.assertEqual(Notification.objects.count(), 0)

        staff_cancelled = self.order()
        self.assertTrue(staff_cancel_unpaid_order(staff_cancelled, self.staff))
        self.assert_notification(staff_cancelled, category=Notification.Category.ORDER, event_type=NotificationEvent.ORDER_CANCELLED)
        self.assertFalse(staff_cancel_unpaid_order(staff_cancelled, self.staff).restored)
        self.assertEqual(Notification.objects.filter(event_type=NotificationEvent.ORDER_CANCELLED).count(), 1)

        stale = self.order(status=Order.Status.PENDING, method=Order.PaymentMethod.MOCK)
        self.assertTrue(
            cancel_unpaid_order(
                order=stale,
                reason="online payment timed out",
                operation_code="STALE_ONLINE_PAYMENT_CANCELLED",
            )
        )
        self.assertEqual(Notification.objects.filter(event_type=NotificationEvent.ORDER_CANCELLED).count(), 2)

    def test_real_order_notification_appears_in_the_center_and_uses_localized_presentation(self):
        order = self.order()
        mark_as_shipped(order, self.staff)
        notification = self.assert_notification(
            order,
            category=Notification.Category.ORDER,
            event_type=NotificationEvent.ORDER_SHIPPED,
        )

        self.client.force_login(self.user)
        with translation.override("en"):
            response = self.client.get(reverse("notifications:center") + "?category=order")
        self.assertContains(response, "Order shipped")
        self.assertEqual(response.context["notification_unread_count"], 1)
        with translation.override("en"):
            opened = self.client.post(reverse("notifications:open", args=[notification.pk]))
            self.assertRedirects(opened, notification.target_url, fetch_redirect_response=False)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

        with translation.override("fa"):
            localized_open = self.client.post(reverse("notifications:open", args=[notification.pk]))
            self.assertRedirects(
                localized_open,
                reverse("orders:order_detail", kwargs={"order_id": order.id}),
                fetch_redirect_response=False,
            )


class NotificationReviewAndSupportIntegrationTests(TestCase):
    def setUp(self):
        call_command("seed_dashboard_roles", verbosity=0)
        self.user = get_user_model().objects.create_user("notification-reviewer", password="secret")
        self.staff = get_user_model().objects.create_user("notification-support-agent", password="secret")
        self.staff.groups.add(self.staff.groups.model.objects.get(name="Support Manager"))
        self.product = Product.objects.create(name="Notification Tile", slug="notification-tile", sku="NOTIFICATION-TILE")

    def test_review_and_review_comment_approvals_notify_once_without_rejection_notifications(self):
        review = Review.objects.create(product=self.product, user=self.user, rating=5, comment="Excellent tile")
        self.assertTrue(approve_review(review, self.staff))
        review_notification = Notification.objects.get(event_type=NotificationEvent.REVIEW_APPROVED)
        self.assertEqual(review_notification.user, self.user)
        self.assertEqual(review_notification.category, Notification.Category.REVIEW)
        self.assertEqual(review_notification.payload, {"product_name": self.product.name})
        self.assertEqual(review_notification.target_url, reverse_notification_target("products:product_detail", kwargs={"slug": self.product.slug}))
        self.assertTrue(approve_review(review, self.staff))
        self.assertEqual(Notification.objects.filter(event_type=NotificationEvent.REVIEW_APPROVED).count(), 1)

        comment = ReviewComment.objects.create(review=review, user=self.user, comment="Thank you")
        self.assertTrue(approve_review_comment(comment, self.staff))
        comment_notification = Notification.objects.get(event_type=NotificationEvent.REVIEW_COMMENT_APPROVED)
        self.assertEqual(comment_notification.user, self.user)
        self.assertEqual(comment_notification.category, Notification.Category.REVIEW)
        self.assertEqual(comment_notification.target_url, reverse_notification_target("products:product_detail", kwargs={"slug": self.product.slug}))
        self.assertTrue(approve_review_comment(comment, self.staff))
        self.assertEqual(Notification.objects.filter(event_type=NotificationEvent.REVIEW_COMMENT_APPROVED).count(), 1)

        rejected_review = Review.objects.create(product=Product.objects.create(name="Second Tile", slug="second-notification-tile", sku="SECOND-NOTIFICATION-TILE"), user=self.user, rating=3, comment="Needs work")
        self.assertTrue(reject_review(rejected_review, self.staff))
        rejected_comment = ReviewComment.objects.create(review=review, user=self.user, comment="Remove this")
        self.assertTrue(reject_review_comment(rejected_comment, self.staff))
        self.assertEqual(Notification.objects.filter(category=Notification.Category.REVIEW).count(), 2)

    def test_article_comment_approval_notifies_its_author_once(self):
        article = Article.objects.create(
            title="Notification article",
            slug="notification-article",
            content="<p>Article</p>",
        )
        comment = ArticleComment.objects.create(
            article=article,
            user=self.user,
            body="Useful article.",
        )

        self.assertTrue(approve_blog_comment(comment, self.staff))

        notification = Notification.objects.get(
            event_type=NotificationEvent.ARTICLE_COMMENT_APPROVED,
        )
        self.assertEqual(notification.user, self.user)
        self.assertEqual(notification.category, Notification.Category.CONTENT)
        self.assertEqual(notification.payload, {"article_title": article.title})
        self.assertEqual(
            notification.target_url,
            reverse_notification_target(
                "blog:article_detail", kwargs={"slug": article.slug}
            ),
        )
        self.assertTrue(approve_blog_comment(comment, self.staff))
        self.assertEqual(
            Notification.objects.filter(
                event_type=NotificationEvent.ARTICLE_COMMENT_APPROVED,
            ).count(),
            1,
        )

    def test_unapproved_parent_comment_cannot_notify(self):
        review = Review.objects.create(product=self.product, user=self.user, rating=4, comment="Good")
        parent = ReviewComment.objects.create(review=review, user=self.user, comment="Parent")
        child = ReviewComment.objects.create(review=review, user=self.user, parent=parent, comment="Child")
        self.assertFalse(approve_review_comment(child, self.staff))
        self.assertFalse(Notification.objects.filter(event_type=NotificationEvent.REVIEW_COMMENT_APPROVED).exists())

    def test_staff_reply_notifies_once_customer_reply_does_not_and_independent_status_changes_notify(self):
        category = TicketCategory.objects.create(name="Notification support")
        ticket = create_ticket(user=self.user, category=category, subject="Delivery question", message="Where is my order?")

        create_ticket_reply(ticket=ticket, author=self.staff, message="We are checking this.", is_staff_reply=True)
        reply_notification = Notification.objects.get(event_type=NotificationEvent.SUPPORT_REPLY)
        self.assertEqual(reply_notification.user, self.user)
        self.assertEqual(reply_notification.category, Notification.Category.SUPPORT)
        self.assertEqual(reply_notification.payload, {"ticket_subject": ticket.subject})
        self.assertEqual(reply_notification.target_url, reverse_notification_target("support:ticket_detail", kwargs={"ticket_id": ticket.id}))
        self.assertFalse(Notification.objects.filter(event_type=NotificationEvent.SUPPORT_STATUS_CHANGED).exists())

        create_ticket_reply(ticket=ticket, author=self.user, message="Thank you")
        self.assertEqual(Notification.objects.filter(category=Notification.Category.SUPPORT).count(), 1)
        resolve_ticket(ticket=ticket, actor=self.staff)
        resolve_ticket(ticket=ticket, actor=self.staff)
        close_ticket(ticket=ticket, actor=self.staff)
        close_ticket(ticket=ticket, actor=self.staff)
        statuses = list(Notification.objects.filter(event_type=NotificationEvent.SUPPORT_STATUS_CHANGED).order_by("created_at"))
        self.assertEqual(len(statuses), 2)
        self.assertEqual(statuses[0].payload, {"ticket_subject": ticket.subject, "status": TicketStatus.RESOLVED})
        self.assertEqual(statuses[1].payload, {"ticket_subject": ticket.subject, "status": TicketStatus.CLOSED})

        with translation.override("en"):
            self.assertIn("Closed", get_notification_presentation(statuses[1]).message)
        with translation.override("fa"):
            self.assertNotIn("closed", get_notification_presentation(statuses[1]).message)

    def test_real_n4_notifications_render_in_the_center_categories(self):
        review = Review.objects.create(product=self.product, user=self.user, rating=5, comment="Beautiful")
        approve_review(review, self.staff)
        category = TicketCategory.objects.create(name="N4 support")
        ticket = create_ticket(user=self.user, category=category, subject="N4 ticket", message="Help")
        create_ticket_reply(ticket=ticket, author=self.staff, message="Answered", is_staff_reply=True)

        self.client.force_login(self.user)
        with translation.override("en"):
            review_response = self.client.get(reverse("notifications:center") + "?category=review")
            support_response = self.client.get(reverse("notifications:center") + "?category=support")
        with translation.override("fa"):
            persian_review_response = self.client.get(reverse("notifications:center") + "?category=review")
        self.assertContains(review_response, "Review approved")
        self.assertContains(support_response, "New support reply")
        self.assertContains(persian_review_response, "دیدگاه تأیید شد")
        self.assertNotContains(persian_review_response, "review_approved")
        self.assertEqual(review_response.context["notification_unread_count"], 2)


class NotificationReadStateHardeningTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("notification-read-user", password="secret")
        self.other_user = get_user_model().objects.create_user("notification-read-other", password="secret")
        self.factory = RequestFactory()

    def notification(self, *, user=None, category=Notification.Category.ORDER, event_type=NotificationEvent.ORDER_SHIPPED, number=1, target_url=""):
        return create_notification(
            user=user or self.user,
            category=category,
            event_type=event_type,
            payload={"order_number": number},
            target_url=target_url,
        )

    def header_context(self, user=None):
        request = self.factory.get("/")
        request.user = user or self.user
        return notification_header(request)

    def test_dropdown_contains_only_unread_and_preview_does_not_change_read_state(self):
        unread_one = self.notification(number=101)
        unread_two = self.notification(number=102)
        read_one = self.notification(number=103)
        read_two = self.notification(number=104)
        mark_notification_read(notification=read_one, user=self.user)
        mark_notification_read(notification=read_two, user=self.user)

        context = self.header_context()
        self.assertEqual(context["notification_unread_count"], 2)
        self.assertEqual([item.notification.pk for item in context["notification_previews"]], [unread_two.pk, unread_one.pk])
        self.assertEqual(Notification.objects.filter(user=self.user, is_read=False).count(), 2)

        self.client.force_login(self.user)
        with translation.override("en"):
            center = self.client.get(reverse("notifications:center"))
        self.assertEqual(
            sum(not item.notification.is_read for item in center.context["notification_items"]),
            2,
        )
        self.assertEqual(
            sum(item.notification.is_read for item in center.context["notification_items"]),
            2,
        )
        self.assertContains(center, 'data-notification-state="read"')
        self.assertContains(center, 'data-notification-state="unread"')
        self.assertContains(center, 'Order #<bdi dir="auto">103</bdi> has been shipped.')

    def test_open_and_mark_all_clear_preview_without_deleting_history(self):
        opened = self.notification(number=201, target_url="")
        remaining = self.notification(number=202)
        other = self.notification(user=self.other_user, number=203)
        self.client.force_login(self.user)

        with translation.override("en"):
            self.client.post(reverse("notifications:open", args=[opened.pk]))
        opened.refresh_from_db()
        self.assertTrue(opened.is_read)
        self.assertIsNotNone(opened.read_at)
        self.assertEqual(self.header_context()["notification_unread_count"], 1)
        self.assertEqual([item.notification.pk for item in self.header_context()["notification_previews"]], [remaining.pk])

        created_at = remaining.created_at
        with translation.override("en"):
            self.client.post(reverse("notifications:mark_all_read"))
        remaining.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(remaining.is_read)
        self.assertEqual(remaining.created_at, created_at)
        self.assertFalse(other.is_read)
        self.assertEqual(self.header_context()["notification_unread_count"], 0)
        self.assertEqual(self.header_context()["notification_previews"], [])
        with translation.override("en"):
            center = self.client.get(reverse("notifications:center"))
        self.assertContains(center, 'Order #<bdi dir="auto">201</bdi> has been shipped.')
        self.assertContains(center, 'Order #<bdi dir="auto">202</bdi> has been shipped.')
        self.assertContains(center, 'data-notification-state="read"', count=2)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 2)

    def test_categories_keep_read_history_and_header_queries_are_bounded(self):
        read_order = self.notification(number=301)
        mark_notification_read(notification=read_order, user=self.user)
        unread_order = self.notification(number=302)
        read_support = self.notification(
            category=Notification.Category.SUPPORT,
            event_type=NotificationEvent.SUPPORT_REPLY,
            number=303,
        )
        mark_notification_read(notification=read_support, user=self.user)

        self.client.force_login(self.user)
        orders = self.client.get(reverse("notifications:center") + "?category=order")
        self.assertEqual([item.notification.pk for item in orders.context["notification_items"]], [unread_order.pk, read_order.pk])

        for number in range(100):
            self.notification(number=400 + number)
        with self.assertNumQueries(2):
            context = self.header_context()
        self.assertEqual(context["notification_unread_count"], 101)
        self.assertEqual(len(context["notification_previews"]), 5)

        anonymous_request = self.factory.get("/")
        anonymous_request.user = AnonymousUser()
        with self.assertNumQueries(0):
            anonymous_context = notification_header(anonymous_request)
        self.assertEqual(anonymous_context, {"notification_unread_count": 0, "notification_previews": []})

    def test_malformed_and_unsafe_persisted_notifications_render_and_open_safely(self):
        legacy = Notification.objects.create(
            user=self.user,
            category=Notification.Category.ORDER,
            event_type=NotificationEvent.ORDER_SHIPPED,
            payload=[],
            target_url="",
        )
        unsafe = self.notification(number=501)
        self.client.force_login(self.user)
        with translation.override("en"):
            center = self.client.get(reverse("notifications:center"))
        self.assertContains(center, "Order shipped")
        self.assertNotContains(center, "[]")

        for target in ("https://evil.example/", "//evil.example/", "javascript:alert(1)"):
            Notification.objects.filter(pk=unsafe.pk).update(target_url=target, is_read=False, read_at=None)
            response = self.client.post(reverse("notifications:open", args=[unsafe.pk]))
            self.assertRedirects(response, reverse("notifications:center"), fetch_redirect_response=False)
        legacy.refresh_from_db()
        self.client.post(reverse("notifications:open", args=[legacy.pk]))
        legacy.refresh_from_db()
        self.assertTrue(legacy.is_read)
