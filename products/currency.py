"""Currency primitives shared by catalog pricing and orders."""

from django.db import models
from django.utils.translation import gettext_lazy


class Currency(models.TextChoices):
    USD = "USD", "USD"
    TOMAN = "IRT", gettext_lazy("Toman")
