"""Small, decoder-backed validation helpers for all project upload boundaries."""
from pathlib import PurePosixPath
import warnings

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from PIL import Image, UnidentifiedImageError


ALLOWED_RASTER_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
ALLOWED_RASTER_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
MAX_PUBLIC_IMAGE_BYTES = 10 * 1024 * 1024
MAX_SUPPORT_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8_000
MAX_IMAGE_PIXELS = 24_000_000


def safe_upload_filename(name, fallback="upload"):
    """Return a display-safe basename; storage remains responsible for its path."""
    return PurePosixPath((name or fallback).replace("\\", "/")).name[:255] or fallback


def validate_raster_image(upload, *, max_bytes=MAX_PUBLIC_IMAGE_BYTES):
    """Require a decodable JPEG, PNG, or WEBP within byte and pixel limits."""
    if upload.size > max_bytes:
        raise ValidationError(_("The image exceeds the maximum upload size."))
    filename = safe_upload_filename(upload.name)
    if PurePosixPath(filename.lower()).suffix not in ALLOWED_RASTER_SUFFIXES:
        raise ValidationError(_("Only JPEG, PNG, and WEBP images are allowed."))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(upload)
            image_format = image.format
            image.verify()
            upload.seek(0)
            image = Image.open(upload)
            width, height = image.size
            if (
                image_format not in ALLOWED_RASTER_FORMATS
                or width > MAX_IMAGE_DIMENSION
                or height > MAX_IMAGE_DIMENSION
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise ValidationError(_("The image format or dimensions are not allowed."))
            image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError, ValueError):
        raise ValidationError(_("Upload a valid JPEG, PNG, or WEBP image."))
    finally:
        upload.seek(0)
    return upload


def validate_raster_images(uploads, *, max_bytes=MAX_PUBLIC_IMAGE_BYTES):
    """Validate every image before a caller starts its authoritative write."""
    return [validate_raster_image(upload, max_bytes=max_bytes) for upload in uploads]


def validate_pdf(upload, *, max_bytes):
    """Lightweight PDF identity validation; this is not malware scanning."""
    if upload.size > max_bytes:
        raise ValidationError(_("The PDF exceeds the maximum upload size."))
    filename = safe_upload_filename(upload.name)
    try:
        header = upload.read(5)
    finally:
        upload.seek(0)
    if PurePosixPath(filename.lower()).suffix != ".pdf" or header != b"%PDF-":
        raise ValidationError(_("Only valid PDF documents may be uploaded."))
    return upload


def validate_raster_or_pdf(upload, *, max_image_bytes, max_pdf_bytes):
    suffix = PurePosixPath(safe_upload_filename(upload.name).lower()).suffix
    if suffix == ".pdf":
        return validate_pdf(upload, max_bytes=max_pdf_bytes)
    return validate_raster_image(upload, max_bytes=max_image_bytes)
