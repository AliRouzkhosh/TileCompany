################# dashboard/urls/orders.py #################
from django.urls import path

from dashboard.views.orders import (
    all_orders,
    approve_refund,
    attention_queue,
    bulk_action,
    cancel_order,
    deliver_order,
    fulfillment_queue,
    hold_order,
    in_transit_queue,
    order_detail,
    process_refund,
    refund_queue,
    refund_processing_queue,
    request_refund_information,
    reject_refund,
    resume_order,
    ship_order,
)

urlpatterns = [
    path(
        "fulfillment/",
        fulfillment_queue,
        name="fulfillment_queue",
    ),
    path(
        "orders/in-transit/",
        in_transit_queue,
        name="in_transit_queue",
    ),

    path(
        "refunds/",
        refund_queue,
        name="refund_queue",
    ),
    path(
        "refunds/processing/",
        refund_processing_queue,
        name="refund_processing_queue",
    ),

    path(
        "attention/",
        attention_queue,
        name="attention_queue",
    ),

    path(
        "orders/",
        all_orders,
        name="all_orders",
    ),

    path(
        "orders/<int:order_id>/",
        order_detail,
        name="order_detail",
    ),

    path(
        "orders/<int:order_id>/ship/",
        ship_order,
        name="ship_order",
    ),

    path(
        "orders/<int:order_id>/deliver/",
        deliver_order,
        name="deliver_order",
    ),

    path(
        "orders/<int:order_id>/approve-refund/",
        approve_refund,
        name="approve_refund",
    ),

    path(
        "orders/<int:order_id>/reject-refund/",
        reject_refund,
        name="reject_refund",
    ),
    path(
        "orders/<int:order_id>/request-refund-information/",
        request_refund_information,
        name="request_refund_information",
    ),

    path(
        "orders/<int:order_id>/process-refund/",
        process_refund,
        name="process_refund",
    ),

    path(
        "orders/<int:order_id>/cancel/",
        cancel_order,
        name="cancel_order",
    ),

    path(
        "orders/<int:order_id>/hold/",
        hold_order,
        name="hold_order",
    ),

    path(
        "orders/<int:order_id>/resume/",
        resume_order,
        name="resume_order",
    ),

    path(
        "bulk-action/",
        bulk_action,
        name="bulk_action",
    ),
]
