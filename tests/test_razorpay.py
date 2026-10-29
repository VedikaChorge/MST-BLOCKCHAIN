import hashlib
import hmac
import json

import pytest

import payments
from conftest import post, signup_and_login

KEY_ID = "rzp_test_unittestkey"
KEY_SECRET = "unit-test-secret"
WEBHOOK_SECRET = "unit-test-webhook-secret"
ORDER_ID = "order_TEST123"
PAYMENT_ID = "pay_TEST456"


class FakeRazorpay(payments.RazorpayGateway):
    """Real signature verification; order creation stubbed so tests never call Razorpay."""

    def __init__(self, fail=False, mode="test"):
        super().__init__(KEY_ID, KEY_SECRET, webhook_secret=WEBHOOK_SECRET, mode=mode)
        self.fail = fail

    def create_order(self, amount, receipt):
        if self.fail:
            raise payments.PaymentError("Razorpay unavailable")
        return ORDER_ID


def sign(order_id, payment_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def razorpay(app, monkeypatch):
    fake = FakeRazorpay()
    monkeypatch.setattr(app, "gateway", fake)
    return fake


def start(client, kind="evidence"):
    response = post(client, "/billing/checkout", {"kind": kind})
    return response, response.headers["Location"].rstrip("/").split("/")[-1]


def credits(app, username="alice"):
    with app.db() as c:
        return c.execute("SELECT evidence_credits FROM users WHERE username=?", (username,)).fetchone()["evidence_credits"]


def confirm(client, reference, order_id=ORDER_ID, payment_id=PAYMENT_ID, signature=None, **kw):
    data = {"razorpay_order_id": order_id, "razorpay_payment_id": payment_id,
            "razorpay_signature": signature if signature is not None else sign(order_id, payment_id)}
    return post(client, f"/billing/checkout/{reference}/razorpay", data, **kw)


@pytest.mark.parametrize("payment_id,signature,expected", [
    (PAYMENT_ID, sign(ORDER_ID, PAYMENT_ID), True),
    (PAYMENT_ID, sign(ORDER_ID, PAYMENT_ID, "wrong-secret"), False),
    ("pay_OTHER", sign(ORDER_ID, PAYMENT_ID), False),
    (PAYMENT_ID, "", False),
])
def test_verify_signature_accepts_only_razorpay_hmac_of_order_and_payment(payment_id, signature, expected):
    gateway = payments.RazorpayGateway(KEY_ID, KEY_SECRET)

    assert gateway.verify_signature(ORDER_ID, payment_id, signature) is expected


@pytest.mark.parametrize("key_id,secret,mode,expected", [
    ("", "", "", None),
    (KEY_ID, "", "", None),
    (KEY_ID, KEY_SECRET, "", "test"),
    ("rzp_live_realkey", KEY_SECRET, "live", "live"),
    ("rzp_live_realkey", KEY_SECRET, "", ValueError),
    (KEY_ID, KEY_SECRET, "live", ValueError),
    (KEY_ID, KEY_SECRET, "production", ValueError),
])
def test_from_env_key_type_must_match_razorpay_mode(monkeypatch, key_id, secret, mode, expected):
    monkeypatch.setenv("RAZORPAY_KEY_ID", key_id)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", secret)
    monkeypatch.setenv("RAZORPAY_MODE", mode)

    if expected is ValueError:
        with pytest.raises(ValueError):
            payments.from_env()
    elif expected is None:
        assert payments.from_env() is None
    else:
        assert payments.from_env().mode == expected


@pytest.mark.parametrize("mode,notice", [("test", b"Test mode \xe2\x80\x94 no real money"), ("live", b"You will be charged real money")])
def test_checkout_page_with_razorpay_loads_popup_and_states_mode(app, client, monkeypatch, mode, notice):
    monkeypatch.setattr(app, "gateway", FakeRazorpay(mode=mode))
    signup_and_login(client, "alice")
    _, reference = start(client)

    page = client.get(f"/billing/checkout/{reference}")

    assert f'data-order-id="{ORDER_ID}"'.encode() in page.data
    assert f'data-key="{KEY_ID}"'.encode() in page.data
    assert b"https://checkout.razorpay.com/v1/checkout.js" in page.data
    assert "https://checkout.razorpay.com" in page.headers["Content-Security-Policy"]
    assert notice in page.data


def test_razorpay_confirm_valid_signature_marks_paid_and_grants_credit(app, client, razorpay):
    signup_and_login(client, "alice")
    _, reference = start(client)

    page = confirm(client, reference, follow_redirects=True).data

    assert credits(app) == 1
    assert PAYMENT_ID.encode() in page and b'pay-paid' in page


@pytest.mark.parametrize("order_id,signature", [(ORDER_ID, "forged"), ("order_OTHER", sign("order_OTHER", PAYMENT_ID))])
def test_razorpay_confirm_bad_signature_or_other_order_grants_nothing(app, client, razorpay, order_id, signature):
    signup_and_login(client, "alice")
    _, reference = start(client)

    page = confirm(client, reference, order_id=order_id, signature=signature, follow_redirects=True).data

    assert b"could not be verified" in page
    assert credits(app) == 0


def test_simulated_completion_is_refused_when_razorpay_is_enabled(app, client, razorpay):
    signup_and_login(client, "alice")
    _, reference = start(client)

    response = post(client, f"/billing/checkout/{reference}/complete", {"outcome": "success"})

    assert response.status_code == 400
    assert credits(app) == 0


def test_checkout_razorpay_order_creation_failure_returns_to_billing(app, client, monkeypatch):
    monkeypatch.setattr(app, "gateway", FakeRazorpay(fail=True))
    signup_and_login(client, "alice")

    response, _ = start(client)
    page = client.get("/billing").data

    assert response.headers["Location"].endswith("/billing")
    assert b"Could not start the Razorpay payment" in page


def webhook_body(order_id=ORDER_ID, payment_id=PAYMENT_ID, amount=4900, event="payment.captured"):
    return json.dumps({"event": event, "payload": {"payment": {"entity": {
        "id": payment_id, "order_id": order_id, "amount": amount, "currency": "INR", "status": "captured"}}}}).encode()


def send_webhook(client, body, secret=WEBHOOK_SECRET):
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhooks/razorpay", data=body, content_type="application/json", headers={"X-Razorpay-Signature": signature})


def test_webhook_payment_captured_credits_pending_payment_once(app, client, razorpay):
    signup_and_login(client, "alice")
    start(client)

    first = send_webhook(client, webhook_body())
    repeat = send_webhook(client, webhook_body())

    assert first.status_code == repeat.status_code == 200
    assert credits(app) == 1


def test_webhook_after_browser_confirmation_does_not_credit_again(app, client, razorpay):
    signup_and_login(client, "alice")
    _, reference = start(client)
    confirm(client, reference)

    send_webhook(client, webhook_body())

    assert credits(app) == 1


@pytest.mark.parametrize("body,secret,status", [
    (webhook_body(), "wrong-secret", 400),
    (webhook_body(amount=100), WEBHOOK_SECRET, 200),
    (webhook_body(order_id="order_UNKNOWN"), WEBHOOK_SECRET, 200),
    (webhook_body(event="payment.failed"), WEBHOOK_SECRET, 200),
])
def test_webhook_forged_mismatched_or_irrelevant_event_grants_nothing(app, client, razorpay, body, secret, status):
    signup_and_login(client, "alice")
    start(client)

    response = send_webhook(client, body, secret)

    assert response.status_code == status
    assert credits(app) == 0


def test_webhook_without_razorpay_configured_is_not_found(client):
    assert send_webhook(client, webhook_body()).status_code == 404
