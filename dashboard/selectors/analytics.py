from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, Exists, F, OuterRef, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.utils.translation import gettext as _, ngettext

from orders.models import Order, OrderItem
from products.currency import Currency
from products.commerce_presentation import quantity_phrase
from products.models import ProductVariant, ProductView

from .catalog_reporting import (
    LEGACY_UNKNOWN_UNIT,
    project_purchased_lines,
    quantity_by_selling_unit,
)


ZERO = Decimal("0.00")


@dataclass(frozen=True, slots=True)
class AnalyticsPeriod:
    start: datetime
    end: datetime
    previous_start: datetime
    previous_end: datetime
    label: str
    code: str


def resolve_analytics_period(params, *, now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    code = params.get("range", "30d")
    start_raw, end_raw = params.get("start"), params.get("end")
    if start_raw and end_raw:
        try:
            start_day = datetime.strptime(start_raw, "%Y-%m-%d").date()
            end_day = datetime.strptime(end_raw, "%Y-%m-%d").date()
            if end_day < start_day:
                raise ValueError
            code, label = "custom", f"{start_day:%b %d, %Y} – {end_day:%b %d, %Y}"
        except ValueError:
            code, start_day, end_day, label = "30d", today - timedelta(days=29), today, _("Last 30 days")
    elif code == "today":
        start_day = end_day = today; label = _("Today")
    elif code in {"7d", "30d", "90d"}:
        days = int(code[:-1]); start_day = today - timedelta(days=days - 1); end_day = today; label = ngettext("Last %(count)d day", "Last %(count)d days", days) % {"count": days}
    elif code == "month":
        start_day = today.replace(day=1); end_day = today; label = _("This month")
    elif code == "year":
        start_day = today.replace(month=1, day=1); end_day = today; label = _("This year")
    else:
        code, start_day, end_day, label = "30d", today - timedelta(days=29), today, _("Last 30 days")
    start = timezone.make_aware(datetime.combine(start_day, time.min))
    end = timezone.make_aware(datetime.combine(end_day + timedelta(days=1), time.min))
    duration = end - start
    return AnalyticsPeriod(start, end, start - duration, start, label, code)


def _sum(queryset, field="total_amount"):
    return queryset.aggregate(value=Coalesce(Sum(field), ZERO))["value"]


def _currency_sums(queryset, field="total_amount"):
    """Return monetary aggregates partitioned by their authoritative currency."""
    return {
        row["currency"]: row["value"]
        for row in queryset.values("currency").annotate(value=Coalesce(Sum(field), ZERO))
    }


def _percent_change(value, previous):
    return None if not previous else round((value - previous) / previous * 100, 1)


def _comparison(value, previous):
    return {"value": value, "previous_value": previous, "change_percent": _percent_change(value, previous)}


def _paid_orders(period):
    return Order.objects.filter(is_paid=True, paid_at__gte=period.start, paid_at__lt=period.end)


def _processed_refunds(period):
    return Order.objects.filter(refund_status=Order.RefundStatus.PROCESSED, refund_processed_at__gte=period.start, refund_processed_at__lt=period.end)


def get_financial_metrics(period):
    paid, previous_paid = _paid_orders(period), Order.objects.filter(is_paid=True, paid_at__gte=period.previous_start, paid_at__lt=period.previous_end)
    refunds, previous_refunds = _processed_refunds(period), Order.objects.filter(refund_status=Order.RefundStatus.PROCESSED, refund_processed_at__gte=period.previous_start, refund_processed_at__lt=period.previous_end)
    gross, previous_gross = _currency_sums(paid), _currency_sums(previous_paid)
    refund_value, previous_refund_value = _currency_sums(refunds), _currency_sums(previous_refunds)
    paid_count, previous_paid_count = paid.count(), previous_paid.count()
    liability = _currency_sums(Order.objects.filter(refund_status=Order.RefundStatus.APPROVED))
    by_currency = []
    for currency in Currency:
        value = gross.get(currency, ZERO)
        previous_value = previous_gross.get(currency, ZERO)
        refunds_value = refund_value.get(currency, ZERO)
        previous_refunds_value = previous_refund_value.get(currency, ZERO)
        currency_paid_count = paid.filter(currency=currency).count()
        previous_currency_paid_count = previous_paid.filter(currency=currency).count()
        net = value - refunds_value
        previous_net = previous_value - previous_refunds_value
        by_currency.append({
            "currency": currency,
            "gross_sales": _comparison(value, previous_value),
            "processed_refunds": _comparison(refunds_value, previous_refunds_value),
            "net_revenue": _comparison(net, previous_net),
            "net_aov": _comparison(net / currency_paid_count if currency_paid_count else ZERO, previous_net / previous_currency_paid_count if previous_currency_paid_count else ZERO),
            "approved_refund_liability": liability.get(currency, ZERO),
        })
    return {
        "paid_orders": _comparison(paid_count, previous_paid_count),
        "by_currency": by_currency,
    }


def get_order_metrics(period):
    orders = Order.objects.filter(created_at__gte=period.start, created_at__lt=period.end)
    created, cancelled, delivered = orders.count(), orders.filter(status=Order.Status.CANCELLED).count(), orders.filter(status=Order.Status.DELIVERED).count()
    paid = _paid_orders(period)
    units = quantity_by_selling_unit(_purchased_lines(period))
    previous_period = AnalyticsPeriod(
        period.previous_start, period.previous_end,
        period.previous_start, period.previous_end, "", "previous",
    )
    previous_units = quantity_by_selling_unit(_purchased_lines(previous_period))
    processed = _processed_refunds(period).count()
    return {"orders_created": created, "paid_orders": paid.count(), "delivered_orders": delivered, "cancelled_orders": cancelled, "quantity_sold_by_unit": units, "previous_quantity_sold_by_unit": previous_units, "cancellation_rate": round(cancelled / created * 100, 1) if created else 0, "refund_order_rate": round(processed / paid.count() * 100, 1) if paid.exists() else 0}


def get_refund_metrics(period):
    requested = Order.objects.filter(refund_requested_at__gte=period.start, refund_requested_at__lt=period.end)
    return {"requested": requested.filter(refund_status=Order.RefundStatus.REQUESTED).count(), "approved": Order.objects.filter(refund_status=Order.RefundStatus.APPROVED).count(), "rejected": requested.filter(refund_status=Order.RefundStatus.REJECTED).count(), "processed": _processed_refunds(period).count(), "active": Order.objects.filter(refund_status__in=[Order.RefundStatus.REQUESTED, Order.RefundStatus.NEEDS_INFORMATION, Order.RefundStatus.APPROVED]).count(), "orders_ever_refund_requested": requested.count()}


def get_operational_metrics():
    return {"ready_to_ship": Order.objects.filter(status=Order.Status.CONFIRMED).filter(Q(is_paid=True) | Q(payment_method=Order.PaymentMethod.COD)).exclude(refund_status__in=[Order.RefundStatus.REQUESTED, Order.RefundStatus.NEEDS_INFORMATION, Order.RefundStatus.APPROVED]).count(), "on_hold": Order.objects.filter(status=Order.Status.ON_HOLD).count(), "refund_review": Order.objects.filter(refund_status__in=[Order.RefundStatus.REQUESTED, Order.RefundStatus.NEEDS_INFORMATION]).count(), "refund_processing": Order.objects.filter(refund_status=Order.RefundStatus.APPROVED).count(), "shipped": Order.objects.filter(status=Order.Status.SHIPPED).count(), "delivered": Order.objects.filter(status=Order.Status.DELIVERED).count()}


def get_inventory_metrics():
    active = ProductVariant.objects.filter(is_active=True, product__is_active=True)
    stock_by_unit = {
        row["price_unit"]: row["value"]
        for row in active.values("price_unit").annotate(value=Coalesce(Sum("stock"), 0))
    }
    return {"active_variants": active.count(), "stock_by_unit": stock_by_unit, "low_stock_count": active.filter(stock__gt=0, stock__lte=F("low_stock_threshold")).count(), "out_of_stock_count": active.filter(stock=0).count(), "low_stock_variants": active.filter(stock__gt=0, stock__lte=F("low_stock_threshold")).select_related("product")[:8], "out_of_stock_variants": active.filter(stock=0).select_related("product")[:8]}


def get_inventory_alerts():
    """Small operational payload for the dashboard home."""
    inventory = get_inventory_metrics()
    return {key: inventory[key] for key in ("low_stock_variants", "out_of_stock_variants")}


def _net_items(period):
    return OrderItem.objects.filter(order__is_paid=True, order__paid_at__gte=period.start, order__paid_at__lt=period.end).exclude(order__refund_status=Order.RefundStatus.PROCESSED)


def _purchased_lines(period):
    items = _net_items(period).select_related("order").order_by("id")
    return project_purchased_lines(items)


def _quantity_display(quantities):
    parts = []
    for code, quantity in sorted(quantities.items()):
        if code == LEGACY_UNKNOWN_UNIT:
            parts.append(_("%(quantity)s legacy / unknown") % {"quantity": quantity})
        else:
            parts.append(quantity_phrase(code, quantity))
    return ", ".join(parts)


def _attribute_identity(attributes):
    return tuple(
        (attribute.scope, attribute.definition_code, attribute.stable_values)
        for attribute in attributes
    )


def get_product_metrics(period):
    product_rows = {}
    variant_rows = {}
    for line in _purchased_lines(period):
        product_key = (
            line.currency, line.product_id or line.product_name,
            line.product_sku, _attribute_identity(line.product_attributes),
        )
        product = product_rows.setdefault(product_key, {
            "currency": line.currency, "product_name": line.product_name,
            "product_sku": line.product_sku, "net_revenue": ZERO,
            "quantity_by_unit": {}, "snapshot_issue_count": 0,
        })
        variant_key = product_key + (
            line.variant_id or line.variant_sku,
            _attribute_identity(line.variant_attributes),
        )
        variant = variant_rows.setdefault(variant_key, {
            "currency": line.currency, "product_name": line.product_name,
            "sku": line.variant_sku, "net_revenue": ZERO,
            "quantity_by_unit": {}, "attributes": line.variant_attributes,
            "snapshot_issue_count": 0,
        })
        for row in (product, variant):
            row["net_revenue"] += line.revenue
            quantities = row["quantity_by_unit"]
            quantities[line.selling_unit_code] = quantities.get(line.selling_unit_code, 0) + line.quantity
            row["snapshot_issue_count"] += bool(line.snapshot_issue)
    for row in (*product_rows.values(), *variant_rows.values()):
        row["quantity_display"] = _quantity_display(row["quantity_by_unit"])
    products = sorted(product_rows.values(), key=lambda row: (row["currency"], -row["net_revenue"], row["product_name"]))[:8]
    products_by_revenue = list(products)
    variants = sorted(variant_rows.values(), key=lambda row: (row["currency"], -row["net_revenue"], row["sku"]))[:8]
    viewed = ProductView.objects.filter(viewed_at__gte=period.start, viewed_at__lt=period.end).values("product_id", "product__name").annotate(view_count=Count("id")).order_by("-view_count")[:8]
    return {"top_products": products, "top_products_by_revenue": products_by_revenue, "top_variants": variants, "top_viewed": viewed}


def get_customer_metrics(period, *, include_customer_details=True):
    User = get_user_model()
    paid = _paid_orders(period).exclude(refund_status=Order.RefundStatus.PROCESSED)
    customer_ids = paid.values("user_id").distinct()
    prior_paid = Order.objects.filter(user_id=OuterRef("pk"), is_paid=True, paid_at__lt=period.start)
    purchasing_users = User.objects.filter(pk__in=customer_ids).annotate(has_prior_paid=Exists(prior_paid))
    returning = purchasing_users.filter(has_prior_paid=True).count()
    new = purchasing_users.filter(has_prior_paid=False).count()
    customers = ()
    if include_customer_details:
        customers = paid.values("currency", "user_id", "user__username", "user__first_name", "user__last_name").annotate(paid_orders=Count("id"), net_spend=Coalesce(Sum("total_amount"), ZERO)).order_by("currency", "-net_spend")[:8]
    purchasing = purchasing_users.count()
    repeat = paid.values("user_id").annotate(order_count=Count("id")).filter(order_count__gt=1).count()
    average_spend = []
    for currency in Currency:
        currency_customers = paid.filter(currency=currency).values("user_id").distinct().count()
        average_spend.append({"currency": currency, "value": _sum(paid.filter(currency=currency)) / currency_customers if currency_customers else ZERO})
    return {"new_customers": new, "returning_customers": returning, "repeat_customer_rate": round(repeat / purchasing * 100, 1) if purchasing else 0, "average_net_revenue_per_customer": average_spend, "top_customers": customers}


def _series_days(period):
    day = timezone.localdate(period.start)
    last = timezone.localdate(period.end - timedelta(seconds=1))
    while day <= last:
        yield day
        day += timedelta(days=1)


def get_revenue_series(period):
    gross = {(row["day"], row["currency"]): row["value"] for row in _paid_orders(period).annotate(day=TruncDate("paid_at")).values("day", "currency").annotate(value=Coalesce(Sum("total_amount"), ZERO))}
    refunds = {(row["day"], row["currency"]): row["value"] for row in _processed_refunds(period).annotate(day=TruncDate("refund_processed_at")).values("day", "currency").annotate(value=Coalesce(Sum("total_amount"), ZERO))}
    return [{"date": day.isoformat(), "currency": currency, "gross_sales": gross.get((day, currency), ZERO), "refunds": refunds.get((day, currency), ZERO), "net_revenue": gross.get((day, currency), ZERO) - refunds.get((day, currency), ZERO)} for currency in Currency for day in _series_days(period)]


def get_order_series(period):
    base = Order.objects.filter(created_at__gte=period.start, created_at__lt=period.end).annotate(day=TruncDate("created_at")).values("day").annotate(orders_created=Count("id"), paid_orders=Count("id", filter=Q(is_paid=True)))
    values = {row["day"]: row for row in base}
    return [{"date": day.isoformat(), "orders_created": values.get(day, {}).get("orders_created", 0), "paid_orders": values.get(day, {}).get("paid_orders", 0)} for day in _series_days(period)]


def get_analytics_context(period, *, include_customer_details=True):
    return {"period": period, "financial": get_financial_metrics(period), "orders": get_order_metrics(period), "refunds": get_refund_metrics(period), "operations": get_operational_metrics(), "inventory": get_inventory_metrics(), "products": get_product_metrics(period), "customers": get_customer_metrics(period, include_customer_details=include_customer_details), "revenue_series": get_revenue_series(period), "order_series": get_order_series(period)}


def get_analytics_statistics():
    """Compatibility wrapper for existing dashboard-home consumers."""
    return get_analytics_context(resolve_analytics_period({}))
