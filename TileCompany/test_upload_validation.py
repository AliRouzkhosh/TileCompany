from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image

from TileCompany.uploads import (
    MAX_IMAGE_DIMENSION,
    safe_upload_filename,
    validate_pdf,
    validate_raster_image,
)


def image_upload(name="image.png", *, image_format="PNG", content_type="image/png", size=(2, 2)):
    image = Image.new("RGB", size, "white")
    payload = BytesIO()
    image.save(payload, format=image_format)
    return SimpleUploadedFile(name, payload.getvalue(), content_type=content_type)


class UploadValidationTests(SimpleTestCase):
    def test_decoder_accepts_supported_raster_formats(self):
        for name, image_format in (("image.jpg", "JPEG"), ("image.png", "PNG"), ("image.webp", "WEBP")):
            with self.subTest(image_format=image_format):
                self.assertIsNotNone(validate_raster_image(image_upload(name, image_format=image_format)))

    def test_rejects_spoofed_or_active_content(self):
        for name, body, content_type in (
            ("not-an-image.jpg", b"not an image", "image/jpeg"),
            ("image.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
        ):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    validate_raster_image(SimpleUploadedFile(name, body, content_type=content_type))

    def test_rejects_excessive_dimensions_and_preserves_safe_basename(self):
        with self.assertRaises(ValidationError):
            validate_raster_image(image_upload(size=(MAX_IMAGE_DIMENSION + 1, 1)))
        self.assertEqual(safe_upload_filename(r"..\\unsafe\\name.png"), "name.png")

    def test_pdf_requires_extension_and_signature(self):
        valid = SimpleUploadedFile("evidence.pdf", b"%PDF-1.7 private evidence", content_type="application/pdf")
        self.assertIs(validate_pdf(valid, max_bytes=1024), valid)
        with self.assertRaises(ValidationError):
            validate_pdf(SimpleUploadedFile("evidence.pdf", b"not a PDF", content_type="application/pdf"), max_bytes=1024)
