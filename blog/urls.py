################ blogs/urls.py #############
from django.urls import path

from .views import (
    article_detail,
    article_list,
    comment_delete,
    comment_edit,
)

app_name = "blog"

urlpatterns = [
    path("", article_list, name="article_list"),
    path("<slug:slug>/", article_detail, name="article_detail"),
    path(
        "comment/<int:pk>/edit/",
        comment_edit,
        name="comment_edit",
    ),
    path(
        "comment/<int:pk>/delete/",
        comment_delete,
        name="comment_delete",
    ),
]
