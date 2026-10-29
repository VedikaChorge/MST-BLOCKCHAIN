from conftest import CSRF, USER_PASSWORD, login, logout, post, register


def test_login_and_logout_valid_account_grants_then_revokes_dashboard_access(client):
    register(client, "alice")

    login_response = login(client, "alice")
    dashboard_while_logged_in = client.get("/dashboard")
    logout(client)
    dashboard_after_logout = client.get("/dashboard")

    assert login_response.headers["Location"].endswith("/dashboard")
    assert dashboard_while_logged_in.status_code == 200
    assert dashboard_after_logout.status_code == 302
    assert "/login" in dashboard_after_logout.headers["Location"]


def test_post_missing_or_wrong_csrf_token_is_rejected(client):
    with client.session_transaction() as s:
        s["csrf"] = CSRF

    missing = client.post("/login", data={"identity": "admin", "password": "x"})
    wrong = client.post("/login", data={"identity": "admin", "password": "x", "csrf_token": "forged"})

    assert missing.status_code == 400
    assert wrong.status_code == 400


def test_login_after_five_failed_attempts_is_locked_even_with_correct_password(client):
    register(client, "bob")
    for _ in range(5):
        login(client, "bob", "wrong-password-123")

    response = login(client, "bob")

    assert response.status_code == 200
    assert b"Too many failed login attempts" in response.data


def test_change_password_correct_current_password_replaces_password(client):
    register(client, "carol")
    login(client, "carol")

    post(client, "/account/password", {"current_password": USER_PASSWORD, "new_password": "Brand-New-Pass-2", "confirm_password": "Brand-New-Pass-2"})
    logout(client)

    assert login(client, "carol").status_code == 200
    assert login(client, "carol", "Brand-New-Pass-2").headers["Location"].endswith("/dashboard")


def test_change_password_wrong_current_password_keeps_old_password(client):
    register(client, "dave")
    login(client, "dave")

    response = post(client, "/account/password", {"current_password": "not-my-password", "new_password": "Brand-New-Pass-2", "confirm_password": "Brand-New-Pass-2"})
    logout(client)

    assert b"Current password is incorrect" in response.data
    assert login(client, "dave").headers["Location"].endswith("/dashboard")


def test_responses_include_security_headers(client):
    response = client.get("/")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
