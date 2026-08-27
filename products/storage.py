"""Private storage for commercially sensitive inventory import sources."""

from django.conf import settings
from django.core.files.storage import FileSystemStorage


# Deliberately outside MEDIA_ROOT and without a URL.  Production can replace this
# instance with a private object-store backend without changing the import service.
private_inventory_import_storage = FileSystemStorage(
    location=settings.BASE_DIR / "private_inventory_price_import_sources",
    base_url=None,
)
