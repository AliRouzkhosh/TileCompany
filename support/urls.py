############## support/urls.py ###################
# support/urls.py

from django.urls import path

from . import views

app_name = "support"


urlpatterns = [
    # ==========================================
    # Help Center
    # ==========================================

    path(
        "",
        views.support_home,
        name="support_home",
    ),
    path("help/", views.help_list, name="help_list"),
    path("help/<slug:slug>/", views.help_detail, name="help_detail"),
    path("attachments/<int:attachment_id>/download/", views.attachment_download, name="attachment_download"),

    # ==========================================
    # Customer Tickets
    # ==========================================

    path(
        "tickets/",
        views.ticket_list,
        name="ticket_list",
    ),
    path(
        "tickets/new/",
        views.ticket_create,
        name="ticket_create",
    ),
    path(
        "tickets/<int:ticket_id>/",
        views.ticket_detail,
        name="ticket_detail",
    ),
]
