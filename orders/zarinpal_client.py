########## zarinpal_client.py##################3
import requests
from django.conf import settings

# ZarinPal uses different base URLs for sandbox vs live (production)
BASE_URL = (
    "https://sandbox.zarinpal.com/pg/v4/payment"
    if settings.ZARINPAL_SANDBOX
    else "https://api.zarinpal.com/pg/v4/payment"
)
STARTPAY_URL = (
    "https://sandbox.zarinpal.com/pg/StartPay/"
    if settings.ZARINPAL_SANDBOX
    else "https://www.zarinpal.com/pg/StartPay/"
)


def request_payment(amount_toman, description, callback_url, mobile="", email=""):
    """
    Step 1: Ask ZarinPal for an Authority code to start the payment.
    amount_toman must be an integer (ZarinPal works in Toman/Rial — check your account's currency setting).
    Returns the Authority string on success, or raises an exception with the error.
    """
    payload = {
        "merchant_id": settings.ZARINPAL_MERCHANT_ID,
        "amount": int(amount_toman),
        "description": description,
        "callback_url": callback_url,
    }
    if mobile:
        payload["metadata"] = {"mobile": mobile}
    if email:
        payload.setdefault("metadata", {})["email"] = email

    resp = requests.post(f"{BASE_URL}/request.json", json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("data") and data["data"].get("code") == 100:
        return data["data"]["authority"]

    errors = data.get("errors", {})
    raise ValueError(f"ZarinPal request failed: {errors}")


def get_payment_url(authority):
    return f"{STARTPAY_URL}{authority}"


def verify_payment(amount_toman, authority):
    """
    Step 2: After the customer is redirected back, verify the payment actually succeeded.
    Returns (success: bool, ref_id: str or None, raw_data: dict)
    """
    payload = {
        "merchant_id": settings.ZARINPAL_MERCHANT_ID,
        "amount": int(amount_toman),
        "authority": authority,
    }
    resp = requests.post(f"{BASE_URL}/verify.json", json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("data") and data["data"].get("code") in (100, 101):
        # 100 = newly verified, 101 = already verified before (treat as success too)
        return True, data["data"].get("ref_id"), data
    return False, None, data