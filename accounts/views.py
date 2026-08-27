################## accounts/views.py ###################33
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView, PasswordResetView
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.http import Http404, HttpResponseRedirect
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _, pgettext
from django.urls import reverse
from django.db import IntegrityError, transaction
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.translation import get_language
from django.utils import timezone
from datetime import timedelta

from TileCompany.client_ip import get_client_ip
from TileCompany.security_events import security_event

from .forms import (
    RegisterForm,
    UserUpdateForm,
    ProfileUpdateForm,
    AddressForm,
    SmsCodeForm,
    SmsPhoneForm,
    SmsSignupCompletionForm,
)

from .models import Address, OtpChallenge, PhoneIdentity
from .otp import request_login_otp, request_phone_verification, verify_login_otp, verify_phone_otp, request_signup_otp, verify_signup_otp
from .email_delivery import schedule_welcome_email

from .selectors import (
    get_profile,
    get_addresses,
)

from .services import (
    build_account_overview,
    update_account,
    create_address,
    update_address,
    delete_address,
)
from .throttling import (
    ThrottleUnavailable,
    password_login_allowed,
    password_reset_allowed,
    signup_allowed,
)


# =====================================================================
# Authentication
# =====================================================================

def _sms_login_available():
    return settings.SMS_ENABLED and settings.SMS_OTP_LOGIN_ENABLED


def _sms_required():
    if not _sms_login_available():
        raise Http404


def _auth_workspace_template(request, full_template, workspace_template):
    """Return a fragment only for the GET-only auth-method enhancement."""
    if request.method == "GET" and request.headers.get("HX-Request") == "true":
        return workspace_template
    return full_template


def _safe_next(request):
    candidate = request.POST.get("next") or request.GET.get("next")
    if candidate and url_has_allowed_host_and_scheme(candidate, {request.get_host()}, request.is_secure()):
        return candidate
    return reverse("accounts:overview")


def _mask_phone(phone):
    return f"{phone[:5]}••••{phone[-3:]}" if phone and len(phone) > 8 else ""


def _otp_request_context(request):
    return {"ip": get_client_ip(request)}


def _throttle_unavailable_form_response(view, form):
    form.add_error(None, _("This service is temporarily unavailable. Please try again later."))
    return view.render_to_response(view.get_context_data(form=form), status=503)


class PasswordLoginView(LoginView):
    template_name = "accounts/login.html"

    def get_template_names(self):
        return [_auth_workspace_template(
            self.request,
            self.template_name,
            "accounts/partials/login_password_workspace.html",
        )]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["sms_login_available"] = _sms_login_available()
        return context

    def post(self, request, *args, **kwargs):
        form = self.get_form()
        try:
            allowed = password_login_allowed(
                client_ip=get_client_ip(request),
                identifier=request.POST.get("username", ""),
            )
        except ThrottleUnavailable:
            security_event("password_login_throttle_unavailable", subject=get_client_ip(request))
            return _throttle_unavailable_form_response(self, form)
        if not allowed:
            security_event("password_login_throttled", subject=get_client_ip(request))
            form.add_error(None, _("Please try again later."))
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)


class ThrottledPasswordResetView(PasswordResetView):
    """Keep reset success responses opaque while suppressing abusive sends."""

    def form_valid(self, form):
        try:
            allowed = password_reset_allowed(
                client_ip=get_client_ip(self.request),
                identifier=form.cleaned_data["email"],
            )
        except ThrottleUnavailable:
            security_event("password_reset_throttle_unavailable", subject=get_client_ip(self.request))
            return _throttle_unavailable_form_response(self, form)
        if not allowed:
            security_event("password_reset_throttled", subject=get_client_ip(self.request))
            return HttpResponseRedirect(self.get_success_url())
        return super().form_valid(form)


def sms_login_request(request):
    _sms_required()
    if request.method == "POST":
        form = SmsPhoneForm(request.POST)
        if form.is_valid():
            try:
                result = request_login_otp(raw_phone=form.cleaned_data["phone"], language=get_language() or "en", request_context=_otp_request_context(request))
            except (ValidationError, PermissionDenied):
                form.add_error("phone", _("Please try again later."))
            else:
                if result.retry_after:
                    messages.info(request, _("Please wait before requesting another code."))
                    return redirect("accounts:sms_login_request")
                request.session["sms_login_next"] = _safe_next(request)
                return redirect("accounts:sms_login_verify", challenge_id=result.challenge_id)
    else:
        form = SmsPhoneForm()
    return render(
        request,
        _auth_workspace_template(request, "accounts/sms_login_request.html", "accounts/partials/login_sms_workspace.html"),
        {"form": form, "next": request.GET.get("next", "")},
    )


def sms_login_verify(request, challenge_id):
    _sms_required()
    if request.method == "POST":
        form = SmsCodeForm(request.POST)
        if form.is_valid():
            result = verify_login_otp(challenge_id=challenge_id, code=form.cleaned_data["code"], request_context=_otp_request_context(request))
            if result.success:
                login(request, result.user)
                return redirect(request.session.pop("sms_login_next", reverse("accounts:overview")))
            form.add_error("code", _("This code is invalid or has expired. Request a new code."))
    else:
        form = SmsCodeForm()
    return _otp_verify_response(request, "accounts/sms_login_verify.html", form, challenge_id, "login")

def otp_resend(request, challenge_id, purpose):
    if request.method != "POST": raise Http404
    _sms_required(); challenge = get_object_or_404(OtpChallenge, id=challenge_id, purpose=purpose)
    context = _otp_request_context(request)
    try:
        if purpose == "login": result = request_login_otp(raw_phone=challenge.phone_e164, language=challenge.communication_language, request_context=context)
        elif purpose == "signup": result = request_signup_otp(raw_phone=challenge.phone_e164, language=challenge.communication_language, request_context=context)
        else:
            if challenge.user_id != request.user.id: raise Http404
            result = request_phone_verification(user=request.user, raw_phone=challenge.phone_e164, language=challenge.communication_language, request_context=context)
    except PermissionDenied:
        messages.error(request, _("Please try again later.")); return redirect(request.path.rsplit("/resend/", 1)[0] + "/")
    if not result.challenge_id:
        messages.info(request, _("Please wait before requesting another code.")); return redirect(request.path.rsplit("/resend/", 1)[0] + "/")
    names = {"login": "accounts:sms_login_verify", "signup": "accounts:sms_signup_verify", "verify_phone": "accounts:phone_verification_verify"}
    return redirect(names[purpose], challenge_id=result.challenge_id)


def sms_signup_request(request):
    _sms_required()
    form = SmsPhoneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            result = request_signup_otp(raw_phone=form.cleaned_data["phone"], language=get_language() or "en", request_context=_otp_request_context(request))
        except (ValidationError, PermissionDenied):
            form.add_error("phone", _("Please try again later."))
        else:
            if result.challenge_id:
                return redirect("accounts:sms_signup_verify", challenge_id=result.challenge_id)
    return render(
        request,
        _auth_workspace_template(request, "accounts/sms_signup_request.html", "accounts/partials/register_sms_workspace.html"),
        {"form": form},
    )


def sms_signup_verify(request, challenge_id):
    _sms_required(); form = SmsCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = verify_signup_otp(challenge_id=challenge_id, code=form.cleaned_data["code"], request_context=_otp_request_context(request))
        if result.account_exists:
            return render(request, "accounts/sms_signup_account_exists.html")
        if result.success:
            request.session["sms_signup_grant"] = {"phone": result.user, "expires": (timezone.now() + timedelta(minutes=10)).timestamp()}
            return redirect("accounts:sms_signup_complete")
        form.add_error("code", _("This code is invalid or has expired. Request a new code."))
    return _otp_verify_response(request, "accounts/sms_signup_verify.html", form, challenge_id, "signup")


def sms_signup_complete(request):
    _sms_required(); grant = request.session.get("sms_signup_grant")
    if not grant or grant["expires"] <= timezone.now().timestamp():
        request.session.pop("sms_signup_grant", None); raise Http404
    form = SmsSignupCompletionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                if PhoneIdentity.objects.filter(phone_e164=grant["phone"]).exists():
                    raise ValidationError("phone conflict")
                user = form.save()
                PhoneIdentity.objects.create(user=user, phone_e164=grant["phone"], verified_at=timezone.now())
        except (ValidationError, IntegrityError):
            form.add_error(None, _("This signup could not be completed. Please request a new code."))
        else:
            request.session.pop("sms_signup_grant", None)
            if user.email:
                schedule_welcome_email(user=user, language=get_language() or "en", account_url=request.build_absolute_uri(reverse("accounts:overview")), support_url=request.build_absolute_uri(reverse("support:support_home")))
            login(request, user); return redirect(_safe_next(request))
    return render(request, "accounts/sms_signup_complete.html", {"form": form})

def register(request):
    """
    Register a new customer account.
    """

    if request.method == "POST":
        form = RegisterForm(request.POST)
        try:
            allowed = signup_allowed(client_ip=get_client_ip(request))
        except ThrottleUnavailable:
            security_event("signup_throttle_unavailable", subject=get_client_ip(request))
            form.add_error(None, _("This service is temporarily unavailable. Please try again later."))
            return render(
                request,
                _auth_workspace_template(request, "accounts/register.html", "accounts/partials/register_standard_workspace.html"),
                {"form": form, "sms_login_available": _sms_login_available()},
                status=503,
            )
        if not allowed:
            security_event("signup_throttled", subject=get_client_ip(request))
            form.add_error(None, _("Please try again later."))
            return render(
                request,
                _auth_workspace_template(request, "accounts/register.html", "accounts/partials/register_standard_workspace.html"),
                {"form": form, "sms_login_available": _sms_login_available()},
                status=429,
            )
        if form.is_valid():
            language = get_language() or "en"
            with transaction.atomic():
                user = form.save()
                schedule_welcome_email(
                    user=user,
                    language=language,
                    account_url=request.build_absolute_uri(reverse("accounts:overview")),
                    support_url=request.build_absolute_uri(reverse("support:support_home")),
                )

            login(request, user)

            messages.success(
                request,
                _("Welcome! Your account has been created successfully."),
            )

            return redirect("products:home")

    else:
        form = RegisterForm()

    return render(
        request,
        _auth_workspace_template(request, "accounts/register.html", "accounts/partials/register_standard_workspace.html"),
        {
            "form": form,
            "sms_login_available": _sms_login_available(),
        },
    )


# =====================================================================
# Account Dashboard
# =====================================================================

@login_required
def overview(request):
    """
    Customer account dashboard.
    """

    context = build_account_overview(
        user=request.user,
    )

    context["active_page"] = "overview"
    context["breadcrumbs"] = [
        {"label": pgettext("account breadcrumb", "My Account"), "url": None},
    ]

    return render(
        request,
        "accounts/overview.html",
        context,
    )


# =====================================================================
# Profile
# =====================================================================

@login_required
def profile(request):
    """
    View and update the customer's profile.
    """

    profile = get_profile(request.user)

    if request.method == "POST":

        user_form = UserUpdateForm(
            request.POST,
            instance=request.user,
        )

        profile_form = ProfileUpdateForm(
            request.POST,
            instance=profile,
        )

        if user_form.is_valid() and profile_form.is_valid():

            update_account(
                user_form=user_form,
                profile_form=profile_form,
            )

            messages.success(
                request,
                pgettext("profile update message", "Your profile has been updated successfully."),
            )

            return redirect("accounts:profile")

    else:

        user_form = UserUpdateForm(
            instance=request.user,
        )

        profile_form = ProfileUpdateForm(
            instance=profile,
        )

    phone_identity = PhoneIdentity.objects.filter(user=request.user).first()

    return render(
        request,
        "accounts/profile.html",
        {
            "user_form": user_form,
            "profile_form": profile_form,
            "profile": profile,
            "phone_identity": phone_identity,
            "phone_identity_masked": _mask_phone(getattr(phone_identity, "phone_e164", "")),
            "sms_login_available": _sms_login_available(),
            "active_page": "profile",
            "breadcrumbs": [
                {"label": pgettext("account breadcrumb", "My Account"), "url": reverse("accounts:overview")},
                {"label": pgettext("account breadcrumb", "Profile"), "url": None},
            ],
        },
    )

# =====================================================================
# Address Book
# =====================================================================

@login_required
def address_list(request):
    """
    Display all customer addresses.
    """

    return render(
        request,
        "accounts/address_list.html",
        {
            "addresses": get_addresses(request.user),
            "active_page": "addresses",
            "breadcrumbs": [
                {"label": pgettext("account breadcrumb", "My Account"), "url": reverse("accounts:overview")},
                {"label": pgettext("address breadcrumb", "Addresses"), "url": None},
            ],
        },
    )

@login_required
def address_create(request):
    """
    Create a new address.
    """

    if request.method == "POST":

        form = AddressForm(request.POST)

        if form.is_valid():

            create_address(
                user=request.user,
                form=form,
            )

            messages.success(
                request,
                pgettext("address message", "Address added successfully."),
            )

            return redirect("accounts:address_list")

    else:

        form = AddressForm()

    return render(
        request,
        "accounts/address_form.html",
        {
            "form": form,
            "page_title": pgettext("address page", "Add address"),
            "active_page": "addresses",
            "breadcrumbs": [
                {"label": pgettext("account breadcrumb", "My Account"), "url": reverse("accounts:overview")},
                {"label": pgettext("address breadcrumb", "Addresses"), "url": reverse("accounts:address_list")},
                {"label": pgettext("address breadcrumb", "Add address"), "url": None},
            ],
        },
    )
@login_required
def address_update(request, pk):
    """
    Edit an existing address.
    """

    address = get_object_or_404(
        Address,
        pk=pk,
        user=request.user,
    )

    if request.method == "POST":

        form = AddressForm(
            request.POST,
            instance=address,
        )

        if form.is_valid():

            update_address(
                user=request.user,
                form=form,
                address=address,
            )

            messages.success(
                request,
                pgettext("address message", "Address updated successfully."),
            )

            return redirect("accounts:address_list")

    else:

        form = AddressForm(instance=address)

    return render(
        request,
        "accounts/address_form.html",
        {
            "form": form,
            "page_title": pgettext("address page", "Edit address"),
            "active_page": "addresses",
            "breadcrumbs": [
                {"label": pgettext("account breadcrumb", "My Account"), "url": reverse("accounts:overview")},
                {"label": pgettext("address breadcrumb", "Addresses"), "url": reverse("accounts:address_list")},
                {"label": pgettext("address breadcrumb", "Edit address"), "url": None},
            ],
        },
    )

@login_required
def address_delete(request, pk):
    """
    Delete an address.
    """

    address = get_object_or_404(
        Address,
        pk=pk,
        user=request.user,
    )

    if request.method == "POST":

        delete_address(address=address)

        messages.success(
            request,
            pgettext("address message", "Address removed."),
        )

        return redirect("accounts:address_list")

    return render(
        request,
        "accounts/address_confirm_delete.html",
        {
            "address": address,
            "active_page": "addresses",
            "breadcrumbs": [
                {"label": pgettext("account breadcrumb", "My Account"), "url": reverse("accounts:overview")},
                {"label": pgettext("address breadcrumb", "Addresses"), "url": reverse("accounts:address_list")},
                {"label": pgettext("address breadcrumb", "Delete address"), "url": None},
            ],
        },
    )


@login_required
def phone_verification_request(request):
    _sms_required()
    if request.method == "POST":
        form = SmsPhoneForm(request.POST)
        if form.is_valid():
            try:
                result = request_phone_verification(user=request.user, raw_phone=form.cleaned_data["phone"], language=get_language() or "en", request_context=_otp_request_context(request))
            except (ValidationError, PermissionDenied):
                form.add_error("phone", _("This number cannot be used. Please try again later."))
            else:
                if result.already_verified:
                    messages.success(request, _("Your SMS login phone is already verified."))
                    return redirect("accounts:profile")
                if result.retry_after:
                    messages.info(request, _("Please wait before requesting another code."))
                else:
                    return redirect("accounts:phone_verification_verify", challenge_id=result.challenge_id)
    else:
        form = SmsPhoneForm(initial={"phone": get_profile(request.user).phone})
    return render(request, "accounts/phone_verification_request.html", {"form": form})


@login_required
def phone_verification_verify(request, challenge_id):
    _sms_required()
    if request.method == "POST":
        form = SmsCodeForm(request.POST)
        if form.is_valid():
            result = verify_phone_otp(user=request.user, challenge_id=challenge_id, code=form.cleaned_data["code"], request_context=_otp_request_context(request))
            if result.success:
                messages.success(request, _("Phone verified. You can now sign in with an SMS code."))
                return redirect("accounts:profile")
            form.add_error("code", _("This code is invalid or has expired. Request a new code."))
    else:
        form = SmsCodeForm()
    return _otp_verify_response(request, "accounts/phone_verification_verify.html", form, challenge_id, "verify_phone")


def _otp_verify_response(request, template_name, form, challenge_id, purpose):
    challenge = OtpChallenge.objects.filter(id=challenge_id, purpose=purpose).first()
    resend_at = challenge.created_at + timedelta(seconds=120) if challenge else timezone.now()
    return render(request, template_name, {"form": form, "challenge_id": challenge_id, "purpose": purpose, "masked_phone": _mask_phone(challenge.phone_e164) if challenge else "", "resend_available_at": resend_at.isoformat(), "resend_available": timezone.now() >= resend_at})
