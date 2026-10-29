import pytest

from conftest import ADMIN_PASSWORD, login, logout, post, register, signup_and_login, upload


@pytest.fixture
def billed(app, monkeypatch):
    monkeypatch.setattr(app, "FREE_EVIDENCE_LIMIT", 2)
    return app


def evidence_count(app, username):
    with app.db() as c:
        return c.execute("SELECT COUNT(*) n FROM evidence e JOIN users u ON u.id=e.user_id WHERE u.username=?", (username,)).fetchone()["n"]


def upload_n(client, n):
    return [upload(client, f"work {i}".encode(), title=f"Work {i}") for i in range(n)]


def start_checkout(client, kind):
    response = post(client, "/billing/checkout", {"kind": kind})
    return response.headers["Location"].rstrip("/").split("/")[-1]


def complete(client, reference, outcome="success", **kw):
    return post(client, f"/billing/checkout/{reference}/complete", {"outcome": outcome}, **kw)


def test_upload_beyond_free_limit_redirects_to_billing_without_saving(billed, client):
    signup_and_login(client, "alice")
    upload_n(client, 2)

    response = upload(client, b"third", title="Third")

    assert response.headers["Location"].endswith("/billing")
    assert evidence_count(billed, "alice") == 2


def test_evidence_credit_payment_allows_exactly_one_more_upload(billed, client):
    signup_and_login(client, "alice")
    upload_n(client, 2)

    complete(client, start_checkout(client, "evidence"))
    paid_upload = upload(client, b"paid", title="Paid")
    next_upload = upload(client, b"over", title="Over")

    assert "/evidence/" in paid_upload.headers["Location"]
    assert next_upload.headers["Location"].endswith("/billing")
    assert evidence_count(billed, "alice") == 3


def test_checkout_failed_payment_grants_nothing(billed, client):
    signup_and_login(client, "alice")
    upload_n(client, 2)

    complete(client, start_checkout(client, "evidence"), outcome="fail")

    assert upload(client, b"x").headers["Location"].endswith("/billing")


def test_checkout_completing_same_payment_twice_grants_once(billed, client):
    signup_and_login(client, "alice")
    upload_n(client, 2)
    reference = start_checkout(client, "evidence")

    complete(client, reference)
    complete(client, reference)
    upload_n(client, 2)

    assert evidence_count(billed, "alice") == 3


def test_checkout_other_users_payment_is_not_found(billed, client):
    signup_and_login(client, "alice")
    reference = start_checkout(client, "evidence")
    logout(client)
    signup_and_login(client, "mallory")

    assert client.get(f"/billing/checkout/{reference}").status_code == 404
    assert complete(client, reference).status_code == 404


def test_checkout_unknown_plan_is_rejected(billed, client):
    signup_and_login(client, "alice")

    assert post(client, "/billing/checkout", {"kind": "lifetime"}).status_code == 400


def test_subscription_allows_unlimited_uploads_until_it_expires(billed, client):
    signup_and_login(client, "alice")
    complete(client, start_checkout(client, "subscription"))

    upload_n(client, 5)
    with billed.db() as c:
        c.execute("UPDATE users SET subscription_until='2000-01-01T00:00:00+00:00' WHERE username='alice'")
    after_expiry = upload(client, b"late", title="Late")

    assert evidence_count(billed, "alice") == 5
    assert after_expiry.headers["Location"].endswith("/billing")


def test_upload_admin_account_is_never_limited(billed, client):
    login(client, "admin", ADMIN_PASSWORD)

    upload_n(client, 3)

    assert evidence_count(billed, "admin") == 3


def test_admin_grant_credits_lets_user_upload_beyond_free_limit(billed, client):
    register(client, "alice")
    with billed.db() as c:
        alice_id = c.execute("SELECT id FROM users WHERE username='alice'").fetchone()["id"]
    login(client, "admin", ADMIN_PASSWORD)
    post(client, f"/admin/users/{alice_id}/credits", {"credits": "2"})
    logout(client)
    login(client, "alice")

    upload_n(client, 5)

    assert evidence_count(billed, "alice") == 4


def test_admin_payments_page_lists_payments_and_paid_revenue(billed, client):
    signup_and_login(client, "alice")
    complete(client, start_checkout(client, "subscription"))
    complete(client, start_checkout(client, "evidence"), outcome="fail")
    logout(client)
    login(client, "admin", ADMIN_PASSWORD)

    page = client.get("/admin/payments").data

    assert b"alice" in page
    assert "Total revenue: ₹299.00".encode() in page
    assert page.count(b'class="payment-row"') == 2
