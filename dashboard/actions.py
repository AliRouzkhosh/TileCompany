############ dashboard/actions.py ####################
from django.contrib import messages

from .services import (
    approve_refund,
    request_refund_information,
    reject_refund,
    process_refund,      # NEW
    mark_as_shipped,
    mark_as_shipped,
    mark_as_delivered,
    cancel_unpaid_order,
    hold_order,
    resume_order,

    approve_blog_comment,
    reject_blog_comment,
    approve_review,
    reject_review,
    approve_review_comment,
    reject_review_comment,
)

# ==========================================================
# Message Helpers
# ==========================================================

def success(request, text):
    messages.success(request, text)


def warning(request, text):
    messages.warning(request, text)


def error(request, text):
    messages.error(request, text)


# ==========================================================
# Bulk Executor
# ==========================================================

ACTION_MAP = {
    "ship": mark_as_shipped,
    "deliver": mark_as_delivered,

    "approve_refund": approve_refund,
    "request_refund_information": request_refund_information,
    "reject_refund": reject_refund,
    "process_refund": process_refund,

    "cancel": cancel_unpaid_order,
    "hold": hold_order,
    "resume": resume_order,

    # ------------------------------------------------------
    # Moderation
    # ------------------------------------------------------

    "approve_blog_comment": approve_blog_comment,
    "reject_blog_comment": reject_blog_comment,

    "approve_review": approve_review,
    "reject_review": reject_review,

    "approve_review_comment": approve_review_comment,
    "reject_review_comment": reject_review_comment,
}


def execute_bulk_action(action_name, orders, staff_user):
    """
    Execute one action on multiple orders.

    Returns:
        (successful_count, failed_count)
    """

    action = ACTION_MAP.get(action_name)

    if action is None:
        return 0, len(orders)

    success_count = 0
    failed_count = 0

    for order in orders:

        if action(order, staff_user):
            success_count += 1
        else:
            failed_count += 1

    return success_count, failed_count


# ==========================================================
# Single Executor
# ==========================================================

def execute_single_action(action_name, order, staff_user,**kwargs,):
    """
    Execute one workflow action on one order.
    """

    action = ACTION_MAP.get(action_name)

    if action is None:
        return False

    return action(order, staff_user, **kwargs,)

def execute_moderation_action(action,obj,moderator,):
    actions = {
        "approve_blog_comment": approve_blog_comment,
        "reject_blog_comment": reject_blog_comment,
        "approve_review": approve_review,
        "reject_review": reject_review,
        "approve_review_comment": approve_review_comment,
        "reject_review_comment": reject_review_comment,
    }

    handler = actions.get(action)

    if handler is None:
        return False

    return handler(obj,moderator,)

