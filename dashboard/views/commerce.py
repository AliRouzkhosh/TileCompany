from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from dashboard.forms_commerce import CommerceSettingsForm
from dashboard.permissions import dashboard_permission_required
from dashboard.services_commerce import StaleCommercePolicy, update_commerce_settings
from orders.pricing import load_policy


@dashboard_permission_required("orders.view_commercepolicy")
def settings(request):
    policy, methods = load_policy()
    if request.method == "POST":
        if not request.user.has_perm("orders.change_commercepolicy") or not request.user.has_perm("orders.change_deliverymethodpolicy"):
            raise PermissionDenied
        form = CommerceSettingsForm(request.POST, policy=policy, methods=methods)
        if form.is_valid():
            try:
                update_commerce_settings(actor=request.user, cleaned_data=form.cleaned_data)
            except StaleCommercePolicy:
                messages.error(request, _("These settings changed while you were editing them. Review the latest values and try again."))
                policy, methods = load_policy()
                form = CommerceSettingsForm(policy=policy, methods=methods)
                return render(request, "dashboard/commerce/settings.html", {"form": form, "policy": policy, "methods": methods}, status=409)
            messages.success(request, _("Commerce settings updated."))
            return redirect("dashboard:commerce:settings")
    else:
        form = CommerceSettingsForm(policy=policy, methods=methods)
    return render(request, "dashboard/commerce/settings.html", {"form": form, "policy": policy, "methods": methods})
