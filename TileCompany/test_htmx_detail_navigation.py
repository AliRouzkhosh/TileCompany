"""Regression coverage for detail links inside bounded HTMX workspaces."""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class HtmxDetailNavigationMarkupTests(SimpleTestCase):
    def _template(self, relative_path):
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    def test_entity_destinations_opt_out_of_workspace_boosting(self):
        cases = {
            "orders/templates/orders/components/order_list_card.html": "orders:order_detail",
            "products/templates/products/components/wishlist_product_card.html": "product.get_absolute_url",
            "blog/templates/blog/components/article_card.html": "article.get_absolute_url",
            "support/templates/support/components/help_discovery.html": "support:help_detail",
            "support/templates/support/components/support_ticket_card.html": "support:ticket_detail",
        }
        for path, destination in cases.items():
            with self.subTest(path=path):
                source = self._template(path)
                start = source.index(destination)
                self.assertIn('hx-boost="false"', source[start:start + 220])

    def test_notification_destination_post_uses_normal_document_navigation(self):
        source = self._template("notifications/templates/notifications/components/notification_item.html")
        self.assertIn('action="{% url \'notifications:open\' item.notification.pk %}" hx-boost="false"', source)

    def test_workspace_roots_remain_limited_to_discovery_controls(self):
        for path, workspace in (
            ("orders/templates/orders/components/orders_workspace.html", "orders-workspace"),
            ("notifications/templates/notifications/components/notification_workspace.html", "notification-workspace"),
            ("support/templates/support/components/ticket_list_workspace.html", "ticket-list-workspace"),
            ("support/templates/support/components/help_discovery.html", "help-discovery"),
            ("blog/templates/blog/components/article_discovery.html", "article-discovery"),
        ):
            source = self._template(path)
            self.assertIn(f'id="{workspace}"', source)
            self.assertIn(f'hx-target="#{workspace}"', source)
