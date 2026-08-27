"""Create or remove deterministic local load-test identities and queue data.

Run this module inside the local-production web image. It refuses every other
environment/database combination and never targets external services.
"""

from __future__ import annotations

import argparse
import os
from decimal import Decimal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "TileCompany.settings")

import django

django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection, transaction
from django.utils import timezone

from accounts.models import Address
from orders.models import Order, OrderItem
from blog.models import Article, ArticleStatus
from products.models import Product, ProductType, ProductVariant, VariantPrice
from support.models import HelpArticle, Ticket, TicketCategory, TicketPriority, TicketReply, TicketStatus


IDENTITY_PREFIX = "loadtest-"
SHOPPER_COUNT = 64
STAFF_COUNT = 32
PASSWORD = os.environ.get("LOADTEST_PASSWORD", "LocalLoadOnly-2026!")


def require_local_production() -> None:
    if settings.APP_ENV != "local_production" or connection.vendor != "postgresql":
        raise SystemExit(
            "Refusing load-test fixture mutation: APP_ENV must be local_production "
            "and the database must be PostgreSQL."
        )


def cleanup_identities() -> int:
    User = get_user_model()
    users = User.objects.filter(username__startswith=IDENTITY_PREFIX)
    count = users.count()
    users.delete()
    return count


def ensure_user(username: str, *, staff_groups: tuple[str, ...] = ()):
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@load.invalid"},
    )
    user.email = f"{username}@load.invalid"
    user.is_active = True
    user.is_staff = False
    user.is_superuser = False
    user.set_password(PASSWORD)
    user.save()
    if staff_groups:
        user.groups.set(Group.objects.filter(name__in=staff_groups))
    else:
        user.groups.clear()
    return user


def ensure_address(user, index: int) -> Address:
    address, _ = Address.objects.update_or_create(
        user=user,
        label="home",
        defaults={
            "first_name": "Load",
            "last_name": f"Shopper {index:03d}",
            "phone": f"+98912000{index:04d}",
            "country": "Iran",
            "province": "Tehran",
            "city": "Tehran",
            "street_address": "LOCAL LOAD TEST FIXTURE - NOT A CUSTOMER ADDRESS",
            "postal_code": f"10000{index:05d}",
            "is_default_shipping": True,
            "is_default_billing": True,
        },
    )
    return address


def seed() -> dict[str, int]:
    require_local_production()
    with transaction.atomic():
        # Reuse a sufficiently populated retained demo dataset. Existing seed
        # commands deliberately refuse to overwrite operator-mutated records,
        # so invoke them only when their whole surface is absent.
        catalog_ready = (
            Product.objects.filter(is_active=True).count() >= 20
            and ProductType.objects.filter(is_active=True).exists()
            and ProductVariant.objects.filter(is_active=True).count() >= 20
            and VariantPrice.objects.exists()
        )
        if not catalog_ready:
            call_command("seed_demo_catalog", "--apply", verbosity=0)
        if not Article.objects.filter(status=ArticleStatus.PUBLISHED).exists():
            call_command("seed_launch_blog", "--apply", verbosity=0)
        if not HelpArticle.objects.filter(is_published=True).exists():
            call_command("seed_launch_help", "--apply", verbosity=0)
        call_command("seed_dashboard_roles", verbosity=0)

        cleanup_identities()
        shoppers = [
            ensure_user(f"{IDENTITY_PREFIX}shopper-{index:03d}")
            for index in range(SHOPPER_COUNT)
        ]
        staff = [
            ensure_user(
                f"{IDENTITY_PREFIX}staff-{index:03d}",
                staff_groups=("Order Manager", "Support Manager"),
            )
            for index in range(STAFF_COUNT)
        ]
        addresses = [ensure_address(user, index) for index, user in enumerate(shoppers)]

        variant = (
            ProductVariant.objects.filter(
                is_active=True,
                stock__gte=4,
                product__is_active=True,
                prices__currency="USD",
            )
            .select_related("product")
            .order_by("pk")
            .first()
        )
        if variant is None:
            raise SystemExit("The demo catalogue did not produce a purchasable USD variant.")
        price = variant.prices.get(currency="USD").amount

        statuses = (
            Order.Status.PENDING,
            Order.Status.CONFIRMED,
            Order.Status.ON_HOLD,
            Order.Status.SHIPPED,
            Order.Status.DELIVERED,
        )
        for index in range(30):
            status = statuses[index % len(statuses)]
            paid = status in {Order.Status.CONFIRMED, Order.Status.SHIPPED, Order.Status.DELIVERED}
            order = Order.objects.create(
                user=shoppers[index % len(shoppers)],
                status=status,
                is_paid=paid,
                full_name=f"LOADTEST Order {index:03d}",
                address="LOCAL LOAD TEST FIXTURE",
                city="Tehran",
                phone="+989120000000",
                shipping_address=addresses[index % len(addresses)],
                payment_method=Order.PaymentMethod.MOCK,
                total_amount=price,
                currency="USD",
                paid_at=timezone.now() if paid else None,
            )
            OrderItem.objects.create(
                order=order,
                product=variant.product,
                variant=variant,
                product_name=variant.product.name,
                unit_price=price,
                quantity=1,
            )

        category = TicketCategory.objects.filter(slug="support").first() or TicketCategory.objects.first()
        if category is None:
            raise SystemExit("The Help Center fixture did not produce a TicketCategory.")
        for index in range(20):
            ticket = Ticket.objects.create(
                user=shoppers[index % len(shoppers)],
                category=category,
                assigned_to=staff[index % len(staff)] if index % 3 == 0 else None,
                subject=f"LOADTEST support case {index:03d}",
                message="Deterministic local load-test ticket; contains no customer data.",
                status=TicketStatus.WAITING_SUPPORT if index % 2 else TicketStatus.OPEN,
                priority=TicketPriority.HIGH if index % 5 == 0 else TicketPriority.MEDIUM,
                last_customer_reply_at=timezone.now(),
            )
            TicketReply.objects.create(
                ticket=ticket,
                author=shoppers[index % len(shoppers)],
                message="Deterministic local load-test reply.",
                is_staff_reply=False,
            )

    return {
        "shoppers": SHOPPER_COUNT,
        "staff": STAFF_COUNT,
        "orders": 30,
        "tickets": 20,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--apply", action="store_true")
    action.add_argument("--cleanup-identities", action="store_true")
    args = parser.parse_args()
    require_local_production()
    if args.cleanup_identities:
        print(f"Removed {cleanup_identities()} local load-test identities and their dependent rows.")
        return
    print(f"Local load-test fixtures ready: {seed()}")


if __name__ == "__main__":
    main()
