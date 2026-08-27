from .models import Review, ReviewComment


def user_can_review_product(user, product):
    """
    One pending review per user + product.
    """
    return not Review.objects.filter(
        user=user,
        product=product,
        is_approved=False,
    ).exists()


def user_can_comment_review(user, review):
    """
    One pending TOP-LEVEL comment per user + review.
    """
    return not ReviewComment.objects.filter(
        user=user,
        review=review,
        parent__isnull=True,
        is_approved=False,
    ).exists()


def user_can_reply_comment(user, parent):
    """
    One pending reply per user + parent comment.
    """
    return not ReviewComment.objects.filter(
        user=user,
        parent=parent,
        is_approved=False,
    ).exists()