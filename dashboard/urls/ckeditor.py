from django.urls import path

from dashboard.views.ckeditor import article_editor_upload


urlpatterns = [
    path("ckeditor-upload/", article_editor_upload, name="ckeditor_upload"),
]
