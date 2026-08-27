from types import SimpleNamespace

from django.contrib.auth.models import User
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.translation import override

from notifications.models import Notification, NotificationEvent


class MobileNotificationDiscoverabilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mobile-member", password="test-pass")

    def create_unread(self, count=1):
        Notification.objects.bulk_create(
            [
                Notification(
                    user=self.user,
                    category=Notification.Category.ORDER,
                    event_type=NotificationEvent.ORDER_CONFIRMED,
                )
                for _ in range(count)
            ]
        )

    def test_anonymous_and_zero_unread_render_no_mobile_menu_badge(self):
        anonymous = self.client.get("/en/")
        self.assertNotContains(anonymous, "data-mobile-menu-notification-count")
        self.client.force_login(self.user)
        zero = self.client.get("/en/")
        self.assertNotContains(zero, "data-mobile-menu-notification-count")
        self.assertContains(zero, 'aria-label="Open navigation"')

    def test_unread_badge_is_bounded_and_accessible(self):
        self.create_unread(100)
        self.client.force_login(self.user)
        response = self.client.get("/en/")
        self.assertContains(response, "data-mobile-menu-notification-count", count=1)
        self.assertContains(response, ">99+</span>", html=False)
        self.assertContains(response, 'aria-label="Open navigation, 100 unread notifications"')

    def test_drawer_notification_is_first_unique_canonical_account_link(self):
        self.create_unread()
        self.client.force_login(self.user)
        response = self.client.get("/en/")
        content = response.content.decode()
        identity = content.index('id="mobileAccountTitle"')
        notification = content.index('href="/en/notifications/"', identity)
        account = content.index('href="/en/accounts/"', identity)
        self.assertLess(identity, notification)
        self.assertLess(notification, account)
        self.assertEqual(content.count('href="/en/notifications/"'), 2)
        self.assertContains(response, "mobile-notification-link is-unread")
        self.assertContains(response, "data-mobile-notification-count", count=1)

    def test_en_fa_labels_and_logical_badge_positioning(self):
        self.create_unread(2)
        self.client.force_login(self.user)
        english = self.client.get("/en/")
        persian = self.client.get("/fa/")
        self.assertContains(english, '<html lang="en" dir="ltr">', html=False)
        self.assertContains(persian, '<html lang="fa" dir="rtl">', html=False)
        self.assertContains(english, 'aria-label="Open navigation, 2 unread notifications"')
        self.assertContains(persian, "اعلان خوانده‌نشده")
        self.assertContains(english, "data-mobile-menu-notification-count", count=1)
        self.assertContains(persian, "data-mobile-menu-notification-count", count=1)
        self.assertContains(english, "-end-0.5")
        self.assertNotContains(english, 'data-mobile-menu-notification-count aria-hidden="false"')

    def test_prepared_partial_context_adds_zero_queries_and_preserves_other_badges(self):
        request = RequestFactory().get("/en/")
        request.user = SimpleNamespace(is_authenticated=True, is_staff=False, username="member", email="")
        context = {
            "request": request,
            "user": request.user,
            "notification_unread_count": 7,
            "cart_count": 12,
            "storefront_navigation": {"sections": [], "view_all_url": "/en/products/"},
        }
        with override("en"), CaptureQueriesContext(connection) as captured:
            header = render_to_string("components/navbar/mobile_nav.html", context)
            drawer = render_to_string("components/navbar/mobile_drawer.html", context)
        self.assertEqual(len(captured), 0)
        self.assertIn("data-mobile-menu-notification-count", header)
        self.assertIn("data-cart-count", header)
        self.assertIn(">12</span>", header)
        self.assertEqual(drawer.count('href="/en/notifications/"'), 1)

    def test_desktop_bell_contract_is_unchanged(self):
        self.create_unread(3)
        self.client.force_login(self.user)
        response = self.client.get("/en/")
        self.assertContains(response, "data-notification-menu", count=1)
        self.assertContains(response, "data-notification-toggle", count=1)
        self.assertContains(response, 'data-lucide="bell"', count=1)
        self.assertContains(response, "data-cart-count", count=0)
