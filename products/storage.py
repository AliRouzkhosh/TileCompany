"""Private storage for commercially sensitive inventory import sources."""

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateInventoryImportStorage(FileSystemStorage):
    """Filesystem storage that refuses public URL generation."""

    def url(self, name):
        raise ValueError("Private inventory import sources do not have public URLs.")


# Deliberately outside MEDIA_ROOT and without a URL. Production can replace this
# instance with a private object-store backend without changing the import service.
private_inventory_import_storage = PrivateInventoryImportStorage(
    location=settings.BASE_DIR / "private_inventory_price_import_sources",
    base_url=None,
)
