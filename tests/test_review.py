import pytest

from conftest import ADMIN_PASSWORD, evidence_id_from, login, logout, post, signup_and_login, upload


@pytest.fixture
def evidence(client):
    signup_and_login(client, "alice")
    eid = evidence_id_from(upload(client, title="Poster"))
    logout(client)
    login(client, "admin", ADMIN_PASSWORD)
    return eid


def review(client, eid, status, note="", **kw):
    return post(client, f"/admin/evidence/{eid}/review", {"status": status, "note": note}, **kw)


def test_review_admin_sets_status_owner_sees_latest_status_note_and_history(client, evidence):
    review(client, evidence, "under_review")
    review(client, evidence, "needs_info", "Please add the original source files.")
    logout(client)
    login(client, "alice")

    page = client.get(f"/evidence/{evidence}").data

    assert b'<span class="badge review-needs_info">Needs more info</span>' in page
    assert b"Please add the original source files." in page
    assert page.count(b'class="review-history-row"') == 2


def test_review_new_upload_starts_pending(client, evidence):
    page = client.get(f"/evidence/{evidence}").data

    assert b'<span class="badge review-pending">Pending review</span>' in page


def test_review_regular_user_is_forbidden(client, evidence):
    logout(client)
    login(client, "alice")

    assert review(client, evidence, "approved").status_code == 403


def test_review_unknown_status_is_rejected(client, evidence):
    assert review(client, evidence, "certified").status_code == 400


@pytest.mark.parametrize("status", ["rejected", "needs_info"])
def test_review_negative_status_without_note_is_refused(client, evidence, status):
    page = review(client, evidence, status, "  ", follow_redirects=True).data

    assert b"Add a note explaining" in page
    assert b'<span class="badge review-pending">Pending review</span>' in page


def test_review_queue_defaults_to_open_items_and_filters_by_status(client, evidence):
    logout(client)
    signup_and_login(client, "bob")
    approved = evidence_id_from(upload(client, b"bob work", title="Bob approved work"))
    logout(client)
    login(client, "admin", ADMIN_PASSWORD)
    review(client, approved, "approved", follow_redirects=True)

    default_queue = client.get("/admin/review").data
    approved_queue = client.get("/admin/review?status=approved").data

    assert b"Poster" in default_queue and b"Bob approved work" not in default_queue
    assert b"Bob approved work" in approved_queue and b"Poster" not in approved_queue
