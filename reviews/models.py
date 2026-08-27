######### review/model.py ##############
from django.db import models
from django.contrib.auth.models import User
from products.models import Product


# Create your models here.
class Review(models.Model):

    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="reviews",
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="reviews",
    )

    rating = models.PositiveSmallIntegerField(
        choices=RATING_CHOICES,
    )

    comment = models.TextField(max_length=2000)

    is_approved = models.BooleanField(
        default=False,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:

        unique_together = ("product", "user")

        ordering = ["-created_at"]

        permissions = [
            ("moderate_reviews", "Can moderate reviews and review comments"),
        ]

    def __str__(self):

        return f"{self.user.username} → {self.product.name}"



class ReviewComment(models.Model):

    review = models.ForeignKey(
        Review,
        on_delete=models.CASCADE,
        related_name="comments",
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="review_comments",
    )

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="replies",
    )

    comment = models.TextField(max_length=2000)

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    is_approved = models.BooleanField(default=False)


    class Meta:

        ordering = ["created_at"]

    def __str__(self):

        return f"{self.user.username} commented on Review #{self.review.id}"
