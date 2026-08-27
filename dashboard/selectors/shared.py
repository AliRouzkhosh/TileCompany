############ dashboard/selectors/shared.py ################

from datetime import timedelta

from django.db.models import Count, F, Sum
from django.utils import timezone
import json
from orders.models import Order, OrderItem
from products.models import Product, ProductView
from django.db.models import Q
from django.core.paginator import Paginator
from blog.models import (
    Comment,
    Article,
    ArticleStatus,
)

from reviews.models import (
    Review,
    ReviewComment,
)

# ==========================================================
# Pagination
# ==========================================================

def paginate_queryset(
    request,
    queryset,
    per_page=20,
):
    """
    Generic pagination helper.
    """

    paginator = Paginator(
        queryset,
        per_page,
    )

    page_number = request.GET.get("page")

    return paginator.get_page(page_number)