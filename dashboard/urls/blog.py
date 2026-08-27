from django.urls import path
from dashboard.views import blog

app_name = "blog"
urlpatterns = [
    path("", blog.article_catalog, name="catalog"), path("create/", blog.article_create, name="create"),
    path("pages/", blog.site_page_list, name="pages"),
    path("pages/<int:pk>/edit/", blog.site_page_edit, name="page_edit"),
    path("pages/<int:pk>/preview/", blog.site_page_preview, name="page_preview"),
    path("<int:pk>/edit/", blog.article_edit, name="edit"), path("<int:pk>/preview/", blog.article_preview, name="preview"),
    path("<int:pk>/publish/", blog.article_publish, name="publish"), path("<int:pk>/unpublish/", blog.article_unpublish, name="unpublish"), path("<int:pk>/delete/", blog.article_delete, name="delete"),
    path("categories/", blog.category_list, name="categories"), path("categories/create/", blog.category_edit, name="category_create"), path("categories/<int:pk>/edit/", blog.category_edit, name="category_edit"), path("categories/<int:pk>/delete/", blog.category_delete, name="category_delete"),
    path("tags/", blog.tag_list, name="tags"), path("tags/create/", blog.tag_edit, name="tag_create"), path("tags/<int:pk>/edit/", blog.tag_edit, name="tag_edit"), path("tags/<int:pk>/delete/", blog.tag_delete, name="tag_delete"),
]
