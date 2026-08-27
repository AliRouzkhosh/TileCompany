############### orders/views.py ###############
"""
Central view registry.

This file re-exports views from the application's feature modules so
urls.py can continue importing from orders.views.
"""

# Cart
from .cart import (
    cart_add_variant,
    cart_update,
    cart_remove,
    cart_detail,
)

# Checkout
from .checkout import (
    shipping,
    review,
    payment
)

# Payment
from .payment import (
    payment_start,
    payment_callback,
    mock_payment,
    local_payment_simulator,
    order_result,
)

# Orders
from .orders import (
    order_list,
    order_detail,
    order_cancel,
    refund_attachment_download,
)
