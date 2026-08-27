from django.urls import path
from dashboard.views import staff_access

app_name = "staff_access"
urlpatterns = [
    path("", staff_access.staff_list, name="list"),
    path("history/", staff_access.staff_history, name="history"),
    path("<int:user_id>/", staff_access.staff_detail, name="detail"),
    path("<int:user_id>/roles/", staff_access.update_roles, name="update_roles"),
]
