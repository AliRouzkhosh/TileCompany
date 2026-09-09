from django.urls import path
from dashboard.views.commerce import settings

app_name = "commerce"
urlpatterns = [path("", settings, name="settings")]
