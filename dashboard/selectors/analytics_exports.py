"""Read-models and serializers for analytics downloads.

Refund reports are selected by refund workflow activity: an order is included when
one of its requested, reviewed, processed, or inventory-restored timestamps falls
inside the selected period.  This makes the report useful for workflow follow-up
without silently including unrelated historical refunds.
"""
import csv
from collections import defaultdict
from io import BytesIO

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from openpyxl import Workbook

from orders.models import Order

from .analytics import ZERO, _net_items, _paid_orders, get_analytics_context
from .catalog_reporting import project_current_inventory, project_purchased_lines


def orders_for_export(period):
    return Order.objects.filter(created_at__gte=period.start, created_at__lt=period.end).select_related("user").order_by("created_at")


def refunds_for_export(period):
    dates = Q(refund_requested_at__gte=period.start, refund_requested_at__lt=period.end) | Q(refund_reviewed_at__gte=period.start, refund_reviewed_at__lt=period.end) | Q(refund_processed_at__gte=period.start, refund_processed_at__lt=period.end) | Q(inventory_restored_at__gte=period.start, inventory_restored_at__lt=period.end)
    return Order.objects.exclude(refund_status=Order.RefundStatus.NONE).filter(dates).select_related("user").order_by("refund_requested_at", "refund_processed_at")


def product_rows_for_export(period):
    rows = {}
    lines = project_purchased_lines(
        _net_items(period).select_related("order").order_by("id")
    )
    for line in lines:
        attributes = _attribute_map(line.product_attributes)
        key = (
            line.currency, line.product_id or line.product_name, line.product_sku,
            line.selling_unit_code, _attribute_identity(line.product_attributes),
        )
        row = rows.setdefault(key, {
            "product": line.product_name, "product_sku": line.product_sku,
            "product_type": line.product_type_code, "currency": line.currency,
            "selling_unit": line.selling_unit_code, "quantity_sold": 0,
            "net_revenue": ZERO, "attributes": attributes,
            "snapshot_issue_count": 0,
        })
        row["quantity_sold"] += line.quantity
        row["net_revenue"] += line.revenue
        row["snapshot_issue_count"] += bool(line.snapshot_issue)
    return sorted(rows.values(), key=lambda row: (row["currency"], -row["net_revenue"], row["product"]))


def variant_rows_for_export(period):
    rows = {}
    lines = project_purchased_lines(
        _net_items(period).select_related("order").order_by("id")
    )
    for line in lines:
        attributes = _attribute_map(
            (*line.product_attributes, *line.variant_attributes)
        )
        key = (
            line.currency, line.product_id or line.product_name,
            line.variant_id or line.variant_sku, line.selling_unit_code,
            _attribute_identity((*line.product_attributes, *line.variant_attributes)),
        )
        row = rows.setdefault(key, {
            "product": line.product_name, "product_sku": line.product_sku,
            "product_type": line.product_type_code, "sku": line.variant_sku,
            "currency": line.currency, "selling_unit": line.selling_unit_code,
            "quantity_sold": 0, "net_revenue": ZERO,
            "attributes": attributes, "operational": dict(line.operational),
            "snapshot_source": line.snapshot_source, "snapshot_issue_count": 0,
        })
        row["quantity_sold"] += line.quantity
        row["net_revenue"] += line.revenue
        row["snapshot_issue_count"] += bool(line.snapshot_issue)
    return sorted(rows.values(), key=lambda row: (row["currency"], -row["net_revenue"], row["sku"]))


def customer_rows_for_export(period):
    paid = _paid_orders(period).exclude(refund_status=Order.RefundStatus.PROCESSED)
    rows = {(row["user_id"], row["currency"]): row for row in paid.values("currency", "user_id", "user__username", "user__first_name", "user__last_name").annotate(paid_orders=Count("id"), net_spend=Coalesce(Sum("total_amount"), ZERO))}
    prior_ids = set(Order.objects.filter(is_paid=True, paid_at__lt=period.start, user_id__in=rows).values_list("user_id", flat=True).distinct())
    processed = Order.objects.filter(refund_status=Order.RefundStatus.PROCESSED, refund_processed_at__gte=period.start, refund_processed_at__lt=period.end, user_id__in=rows).values("user_id").annotate(count=Count("id"))
    processed_map = {row["user_id"]: row["count"] for row in processed}
    result = []
    for (user_id, _currency), row in rows.items():
        name = " ".join(value for value in (row["user__first_name"], row["user__last_name"]) if value) or row["user__username"]
        result.append({"currency": row["currency"], "customer": name, "paid_orders": row["paid_orders"], "processed_refunds": processed_map.get(user_id, 0), "net_spend": row["net_spend"], "customer_type": "Returning" if user_id in prior_ids else "New"})
    return sorted(result, key=lambda row: (row["currency"], -row["net_spend"]))


def inventory_rows_for_export():
    return [
        {
            "product": line.product_name, "product_sku": line.product_sku,
            "product_type": line.product_type_code, "sku": line.variant_sku,
            "selling_unit": line.selling_unit_code, "stock": line.stock,
            "threshold": line.low_stock_threshold, "state": line.inventory_state,
            "attributes": _attribute_map(
                (*line.product_attributes, *line.variant_attributes)
            ),
        }
        for line in project_current_inventory()
    ]


def _attribute_identity(attributes):
    return tuple(
        (attribute.scope, attribute.definition_code, attribute.stable_values)
        for attribute in attributes
    )


def _attribute_map(attributes):
    return {
        (attribute.scope, attribute.definition_code): {
            "label": attribute.label_default,
            "value": "; ".join(attribute.values),
        }
        for attribute in attributes
    }


def _dynamic_attribute_columns(rows):
    labels = defaultdict(set)
    for row in rows:
        for key, value in row.get("attributes", {}).items():
            labels[key].add(value["label"])
    columns = []
    for scope, code in sorted(labels, key=lambda key: (key[0], key[1])):
        label = sorted(labels[(scope, code)], key=lambda value: (value.casefold(), value))[0]
        scope_label = "Product" if scope == "product" else "Variant"
        columns.append(((scope, code), f"{scope_label} • {label} [{code}]"))
    return columns


def _dynamic_operational_columns(rows):
    codes = sorted({code for row in rows for code in row.get("operational", {})})
    return [(code, f"Operational • {code.replace('.', ' / ').replace('_', ' ').title()} [{code}]") for code in codes]


def _rows_with_dynamic_attributes(rows, fixed_keys, columns):
    for row in rows:
        yield [
            *(row[key] for key in fixed_keys),
            *(row.get("attributes", {}).get(key, {}).get("value", "") for key, _ in columns),
        ]


def _variant_export_rows(rows, fixed_keys, attribute_columns, operational_columns):
    for row in rows:
        yield [
            *(row[key] for key in fixed_keys),
            *(row.get("attributes", {}).get(key, {}).get("value", "") for key, _ in attribute_columns),
            *(row.get("operational", {}).get(code, "") for code, _ in operational_columns),
        ]


def _excel_value(value):
    if hasattr(value, "tzinfo") and timezone.is_aware(value):
        return timezone.make_naive(value)
    return value


def _customer(order):
    return order.user.get_full_name() or order.user.username


def write_orders_csv(response, period):
    writer = csv.writer(response); writer.writerow(["Order Number", "Customer", "Created At", "Paid At", "Currency", "Amount", "Status", "Refund Status"])
    for order in orders_for_export(period): writer.writerow([order.user_order_number or order.pk, _customer(order), order.created_at, order.paid_at or "", order.currency, order.total_amount, order.status, order.refund_status])


def write_refunds_csv(response, period):
    writer = csv.writer(response); writer.writerow(["Order Number", "Customer", "Currency", "Order Amount", "Requested Stage", "Refund Status", "Requested At", "Reviewed At", "Processed At", "Inventory Restored At"])
    for order in refunds_for_export(period): writer.writerow([order.user_order_number or order.pk, _customer(order), order.currency, order.total_amount, order.refund_requested_stage, order.refund_status, order.refund_requested_at, order.refund_reviewed_at, order.refund_processed_at, order.inventory_restored_at])


def write_products_csv(response, period):
    writer = csv.writer(response); writer.writerow(["Product", "Product SKU", "Product Type", "Currency", "Selling Unit", "Quantity Sold", "Net Revenue"])
    for row in product_rows_for_export(period): writer.writerow([row["product"], row["product_sku"], row["product_type"], row["currency"], row["selling_unit"], row["quantity_sold"], row["net_revenue"]])


def write_customers_csv(response, period):
    writer = csv.writer(response); writer.writerow(["Customer", "Currency", "Paid Orders", "Processed Refunds", "Net Spend", "Customer Type"])
    for row in customer_rows_for_export(period): writer.writerow([row["customer"], row["currency"], row["paid_orders"], row["processed_refunds"], row["net_spend"], row["customer_type"]])


def build_xlsx(period):
    context = get_analytics_context(period); workbook = Workbook(); summary = workbook.active; summary.title = "Summary"
    summary.append(["TileStore Analytics", period.label]); summary.append(["Currency", "Net Revenue", "Gross Sales", "Processed Refunds"])
    for metric in context["financial"]["by_currency"]:
        summary.append([metric["currency"], metric["net_revenue"]["value"], metric["gross_sales"]["value"], metric["processed_refunds"]["value"]])
    summary.append(["Paid Orders", context["orders"]["paid_orders"]])
    summary.append(["Quantity Sold by Selling Unit"])
    for code, quantity in context["orders"]["quantity_sold_by_unit"].items():
        summary.append([code, quantity])
    product_rows = list(product_rows_for_export(period))
    variant_rows = list(variant_rows_for_export(period))
    inventory_rows = list(inventory_rows_for_export())
    product_columns = _dynamic_attribute_columns(product_rows)
    variant_columns = _dynamic_attribute_columns(variant_rows)
    operational_columns = _dynamic_operational_columns(variant_rows)
    inventory_columns = _dynamic_attribute_columns(inventory_rows)
    sheets = (
        ("Orders", ["Order Number", "Customer", "Created At", "Paid At", "Currency", "Amount", "Status", "Refund Status"], ([o.user_order_number or o.pk, _customer(o), o.created_at, o.paid_at, o.currency, o.total_amount, o.status, o.refund_status] for o in orders_for_export(period))),
        ("Refunds", ["Order Number", "Customer", "Currency", "Amount", "Requested Stage", "Refund Status", "Requested At", "Reviewed At", "Processed At", "Inventory Restored At"], ([o.user_order_number or o.pk, _customer(o), o.currency, o.total_amount, o.refund_requested_stage, o.refund_status, o.refund_requested_at, o.refund_reviewed_at, o.refund_processed_at, o.inventory_restored_at] for o in refunds_for_export(period))),
        ("Products", ["Product", "Product SKU", "Product Type", "Currency", "Selling Unit", "Quantity Sold", "Net Revenue", *[header for _, header in product_columns]], _rows_with_dynamic_attributes(product_rows, ("product", "product_sku", "product_type", "currency", "selling_unit", "quantity_sold", "net_revenue"), product_columns)),
        ("Variants", ["Product", "Product SKU", "Product Type", "Variant SKU", "Currency", "Selling Unit", "Quantity Sold", "Net Revenue", "Snapshot Source", *[header for _, header in variant_columns], *[header for _, header in operational_columns]], _variant_export_rows(variant_rows, ("product", "product_sku", "product_type", "sku", "currency", "selling_unit", "quantity_sold", "net_revenue", "snapshot_source"), variant_columns, operational_columns)),
        ("Customers", ["Customer", "Currency", "Paid Orders", "Processed Refunds", "Net Spend", "Customer Type"], ([r["customer"], r["currency"], r["paid_orders"], r["processed_refunds"], r["net_spend"], r["customer_type"]] for r in customer_rows_for_export(period))),
        ("Inventory", ["Product", "Product SKU", "Product Type", "Variant SKU", "Selling Unit", "Current Stock", "Low Stock Threshold", "Inventory State", *[header for _, header in inventory_columns]], _rows_with_dynamic_attributes(inventory_rows, ("product", "product_sku", "product_type", "sku", "selling_unit", "stock", "threshold", "state"), inventory_columns)),
    )
    for title, headers, rows in sheets:
        sheet = workbook.create_sheet(title); sheet.append(headers)
        for row in rows: sheet.append([_excel_value(value) for value in row])
    output = BytesIO(); workbook.save(output); return output.getvalue()
