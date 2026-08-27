"""Permission-aware CKEditor image upload endpoint for Article editors."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django_ckeditor_5.exceptions import NoImageException
from django_ckeditor_5.forms import UploadFileForm
from django_ckeditor_5.storage_utils import handle_uploaded_file, image_verify

from TileCompany.uploads import validate_raster_image
from dashboard.permissions import dashboard_permission_required


@dashboard_permission_required("blog.add_article", "blog.change_article", any_of=True)
@require_POST
def article_editor_upload(request):
    """Reuse package validation/storage without its ``is_staff`` gate."""
    form = UploadFileForm(request.POST, request.FILES)
    try:
        validate_raster_image(request.FILES["upload"])
        if not getattr(settings, "CKEDITOR_5_ALLOW_ALL_FILE_TYPES", False):
            image_verify(request.FILES["upload"])
    except (KeyError, NoImageException, OSError, SyntaxError, ValidationError):
        return JsonResponse({"error": {"message": _("Invalid image file.")}}, status=400)

    if form.is_valid():
        return JsonResponse({"url": handle_uploaded_file(request.FILES["upload"])})
    if form.errors.get("upload"):
        return JsonResponse({"error": {"message": form.errors["upload"][0]}}, status=400)
    return JsonResponse({"error": {"message": _("Invalid form data")}}, status=400)
