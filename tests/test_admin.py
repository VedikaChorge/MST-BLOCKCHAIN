import pytest

from conftest import ADMIN_PASSWORD, login, logout, post, register, signup_and_login


def user_id(app, username):
    with app.db() as c:
        return c.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]


@pytest.mark.parametrize("path", ["/admin/users", "/admin/audit", "/admin/review", "/admin/payments"])
def test_admin_pages_regular_user_is_forbidden(client, path):
    signup_and_login(client, "alice")

    assert client.get(path).status_code == 403


def test_navigation_regular_user_sees_no_admin_links(client):
    signup_and_login(client, "alice")

    page = client.get("/dashboard").data

    for path in (b"/admin/users", b"/admin/audit", b"/admin/review", b"/admin/payments"):
        assert b'href="' + path + b'"' not in page
    assert b'href="/billing"' in page


def test_admin_role_change_promotes_user_to_admin(app, client):
    register(client, "alice")
    login(client, "admin", ADMIN_PASSWORD)

    post(client, f"/admin/users/{user_id(app, 'alice')}/role", {"role": "admin"})
    logout(client)
    login(client, "alice")

    assert client.get("/admin/users").status_code == 200


def test_admin_role_change_on_own_account_is_refused(app, client):
    login(client, "admin", ADMIN_PASSWORD)

    response = post(client, f"/admin/users/{user_id(app, 'admin')}/role", {"role": "user"}, follow_redirects=True)

    assert b"cannot change your own role" in response.data
    assert client.get("/admin/users").status_code == 200


def test_admin_audit_log_lists_actions_with_username_and_filter(client):
    signup_and_login(client, "alice")
    logout(client)
    login(client, "admin", ADMIN_PASSWORD)

    all_actions = client.get("/admin/audit").data
    logouts_only = client.get("/admin/audit?action=logout").data

    assert b"alice" in all_actions and b'<td class="action">login</td>' in all_actions
    assert b'<td class="action">login</td>' not in logouts_only and b'<td class="action">logout</td>' in logouts_only
