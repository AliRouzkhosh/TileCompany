################# dashboard/urls/home.py #################
from django.urls import path

from dashboard.views.home import dashboard_home

urlpatterns = [
    path(
        "",
        dashboard_home,
        name="home",
    ),
]