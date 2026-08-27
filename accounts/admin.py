############## accounts/admin.py ####################3
from django.contrib import admin
from .models import PhoneIdentity, Profile


from .models import Address, Profile


@admin.register(PhoneIdentity)
class PhoneIdentityAdmin(admin.ModelAdmin):
    list_display = ("user", "phone_e164", "verified_at")
    search_fields = ("user__username", "phone_e164")
    readonly_fields = ("user", "phone_e164", "verified_at", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "phone",
        "is_business",
        "created_at",
    )
    search_fields = (
        "user__username",
        "user__email",
        "phone",
    )


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "label",
        "city",
        "province",
        "country",
        "is_default_shipping",
        "is_default_billing",
    )

    list_filter = (
        "country",
        "province",
        "is_default_shipping",
        "is_default_billing",
    )

    search_fields = (
        "user__username",
        "first_name",
        "last_name",
        "city",
        "postal_code",
    )
