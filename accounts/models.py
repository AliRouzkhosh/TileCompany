############### accounts/models.py #####################
import uuid

from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User
from django.conf import settings
from django.utils.translation import gettext_lazy as _, pgettext_lazy


class Profile(models.Model):
    GENDER_CHOICES = [
        ("male", _("Male")),
        ("female", _("Female")),
        ("other", _("Other")),
        ("prefer_not_to_say", _("Prefer not to say")),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    phone = models.CharField(max_length=20, blank=True)

    birthdate = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=GENDER_CHOICES, blank=True)

    is_business = models.BooleanField(
        default=False,
        help_text=pgettext_lazy("profile field help", "Is this a company/business buyer?"),
    )
    company_name = models.CharField(max_length=200, blank=True)
    company_tax_id = models.CharField(
        max_length=100,
        blank=True,
        help_text=pgettext_lazy("profile field help", "Optional: tax/VAT ID for invoicing"),
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Profile of {self.user.username}"


class PhoneIdentity(models.Model):
    """An explicitly verified OTP-login identity, never a Profile.phone copy."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="phone_identity")
    phone_e164 = models.CharField(max_length=16, unique=True)
    verified_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.phone_e164


class OtpChallenge(models.Model):
    class Purpose(models.TextChoices):
        VERIFY_PHONE = "verify_phone", "Verify phone"
        LOGIN = "login", "Login"
        SIGNUP = "signup", "Signup"

    class SendStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        NOT_SENT = "not_sent", "Not sent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    phone_e164 = models.CharField(max_length=16)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="otp_challenges")
    code_hash = models.CharField(max_length=256)
    communication_language = models.CharField(max_length=2, choices=(("fa", "فارسی"), ("en", "English")), default="fa")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    send_status = models.CharField(max_length=12, choices=SendStatus.choices, default=SendStatus.PENDING)
    sent_at = models.DateTimeField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=128, blank=True)
    send_error_class = models.CharField(max_length=128, blank=True)
    request_ip_fingerprint = models.CharField(max_length=64, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["phone_e164", "purpose", "created_at"]),
            models.Index(fields=["user"]),
            models.Index(fields=["expires_at"]),
            models.Index(fields=["request_ip_fingerprint", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["phone_e164", "purpose"], condition=Q(is_active=True), name="one_active_otp_per_phone_purpose"),
        ]


class Address(models.Model):
    LABEL_CHOICES = [
        ("home", pgettext_lazy("address label", "Home")),
        ("office", pgettext_lazy("address label", "Office")),
        ("warehouse", pgettext_lazy("address label", "Warehouse")),
        ("other", pgettext_lazy("address label", "Other")),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="addresses",
    )

    label = models.CharField(
        max_length=20,
        choices=LABEL_CHOICES,
        default="home",
    )

    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)

    company_name = models.CharField(
        max_length=200,
        blank=True,
    )

    phone = models.CharField(max_length=20)

    country = models.CharField(max_length=100)
    province = models.CharField(max_length=100)
    city = models.CharField(max_length=100)

    street_address = models.TextField()

    postal_code = models.CharField(
        max_length=20,
        blank=True,
    )

    is_default_shipping = models.BooleanField(default=False)
    is_default_billing = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default_shipping", "-created_at"]

    def __str__(self):
        return (
            f"{self.get_label_display()} - "
            f"{self.first_name} {self.last_name}"
        )
