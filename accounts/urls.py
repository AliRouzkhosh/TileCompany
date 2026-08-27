############### accounts/urls.py #####################
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views
from .auth_forms import (
    LoginForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
)

app_name = "accounts"

urlpatterns = [

    # ---------------------------------------------------------
    # Authentication
    # ---------------------------------------------------------

    path(
        "register/",
        views.register,
        name="register",
    ),

    path(
        "login/",
        views.PasswordLoginView.as_view(
            authentication_form=LoginForm,
        ),
        name="login",
    ),
    path("login/sms/", views.sms_login_request, name="sms_login_request"),
    path("login/sms/<uuid:challenge_id>/", views.sms_login_verify, name="sms_login_verify"),
    path("login/sms/<uuid:challenge_id>/resend/", views.otp_resend, {"purpose": "login"}, name="sms_login_resend"),
    path("register/sms/", views.sms_signup_request, name="sms_signup_request"),
    path("register/sms/<uuid:challenge_id>/", views.sms_signup_verify, name="sms_signup_verify"),
    path("register/sms/<uuid:challenge_id>/resend/", views.otp_resend, {"purpose": "signup"}, name="sms_signup_resend"),
    path("register/sms/complete/", views.sms_signup_complete, name="sms_signup_complete"),

    path(
        "logout/",
        auth_views.LogoutView.as_view(
            next_page="products:home",
        ),
        name="logout",
    ),

    # ---------------------------------------------------------
    # Password Reset
    # ---------------------------------------------------------

    path(
        "password-reset/",
        views.ThrottledPasswordResetView.as_view(
            form_class=StyledPasswordResetForm,
            template_name="accounts/password_reset_form.html",
            email_template_name="accounts/emails/password_reset_email.txt",
            html_email_template_name="accounts/emails/password_reset_email.html",
            subject_template_name="accounts/emails/password_reset_subject.txt",
            success_url=reverse_lazy("accounts:password_reset_done"),
        ),
        name="password_reset",
    ),

    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html",
        ),
        name="password_reset_done",
    ),

    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            form_class=StyledSetPasswordForm,
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),

    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),

    # ---------------------------------------------------------
    # Account Center
    # ---------------------------------------------------------

    path(
        "",
        views.overview,
        name="overview",
    ),

    path(
        "profile/",
        views.profile,
        name="profile",
    ),
    path("profile/phone/", views.phone_verification_request, name="phone_verification_request"),
    path("profile/phone/<uuid:challenge_id>/", views.phone_verification_verify, name="phone_verification_verify"),
    path("profile/phone/<uuid:challenge_id>/resend/", views.otp_resend, {"purpose": "verify_phone"}, name="phone_verification_resend"),
    ######## address ###########
    path(
        "addresses/",
        views.address_list,
        name="address_list",
    ),

    path(
        "addresses/add/",
        views.address_create,
        name="address_create",
    ),

    path(
        "addresses/<int:pk>/edit/",
        views.address_update,
        name="address_update",
    ),

    path(
        "addresses/<int:pk>/delete/",
        views.address_delete,
        name="address_delete",
    ),

]
