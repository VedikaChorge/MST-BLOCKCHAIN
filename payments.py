"""Razorpay payment gateway (test or live mode) using the Orders API, checkout signatures and webhooks."""
import base64
import hashlib
import hmac
import json
import os
import urllib.request


class PaymentError(Exception):
    pass


class RazorpayGateway:
    API_URL = "https://api.razorpay.com/v1"

    def __init__(self, key_id, key_secret, currency="INR", webhook_secret="", mode="test"):
        self.key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret
        self.currency = currency
        self.mode = mode

    @property
    def webhooks_enabled(self):
        return bool(self._webhook_secret)

    def create_order(self, amount, receipt):
        """Create a Razorpay order for `amount` in the smallest currency unit (paise) and return its ID."""
        auth = base64.b64encode(f"{self.key_id}:{self._key_secret}".encode()).decode()
        request = urllib.request.Request(
            f"{self.API_URL}/orders", method="POST",
            data=json.dumps({"amount": amount, "currency": self.currency, "receipt": receipt}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            order = json.load(response)
        order_id = str(order.get("id", ""))
        if not order_id.startswith("order_"):
            raise PaymentError("Razorpay did not return an order ID.")
        return order_id

    def verify_signature(self, order_id, payment_id, signature):
        expected = hmac.new(self._key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    def verify_webhook(self, body, signature):
        if not self._webhook_secret:
            return False
        expected = hmac.new(self._webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature or "")


KEY_PREFIXES = {"test": "rzp_test_", "live": "rzp_live_"}


def from_env():
    key_id = os.getenv("RAZORPAY_KEY_ID", "").strip()
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        return None
    mode = os.getenv("RAZORPAY_MODE", "").strip().lower() or "test"
    if mode not in KEY_PREFIXES:
        raise ValueError("RAZORPAY_MODE must be 'test' or 'live'.")
    if not key_id.startswith(KEY_PREFIXES[mode]):
        raise ValueError(f"RAZORPAY_MODE is '{mode}' but RAZORPAY_KEY_ID is not a {mode} key ({KEY_PREFIXES[mode]}...). "
                         "Live keys require RAZORPAY_MODE=live.")
    return RazorpayGateway(key_id, key_secret, os.getenv("RAZORPAY_CURRENCY", "INR"),
                           os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip(), mode)
