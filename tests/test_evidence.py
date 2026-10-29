import hashlib
import io

import pytest
from PIL import Image

from conftest import approve, evidence_id_from, logout, post, signup_and_login, upload


def png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (120, 80), "navy").save(buf, "PNG")
    return buf.getvalue()


def test_upload_valid_file_records_sha256_of_file_bytes(app, client):
    signup_and_login(client, "alice")

    eid = evidence_id_from(upload(client, b"design v1"))
    page = client.get(f"/evidence/{eid}")

    assert hashlib.sha256(b"design v1").hexdigest().encode() in page.data
    assert b"matches recorded hash" in page.data


def test_upload_image_creates_downloadable_watermarked_copy(client):
    signup_and_login(client, "alice")

    eid = evidence_id_from(upload(client, png_bytes(), "logo.png"))
    response = client.get(f"/evidence/{eid}/watermark")

    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).size == (120, 80)


def test_upload_file_already_recorded_warns_about_earlier_record(client):
    signup_and_login(client, "alice")
    first = evidence_id_from(upload(client, b"same bytes"))

    response = upload(client, b"same bytes", follow_redirects=True)

    assert f"already recorded as evidence #{first}".encode() in response.data


@pytest.mark.parametrize("path", ["", "/download", "/report", "/new-version"])
def test_evidence_routes_other_users_evidence_is_forbidden(client, path):
    signup_and_login(client, "alice")
    eid = evidence_id_from(upload(client))
    logout(client)
    signup_and_login(client, "mallory")

    assert client.get(f"/evidence/{eid}{path}").status_code == 403


def test_report_owned_evidence_returns_pdf_certificate(client):
    signup_and_login(client, "alice")
    eid = evidence_id_from(upload(client))

    response = client.get(f"/evidence/{eid}/report")

    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")


def test_upload_new_version_links_to_root_and_increments_version(client):
    signup_and_login(client, "alice")
    v1 = evidence_id_from(upload(client, b"v1", title="Logo"))
    v2 = evidence_id_from(upload(client, b"v2", title="Logo", parent_id=str(v1)))

    v3 = evidence_id_from(upload(client, b"v3", title="Logo", parent_id=str(v2)))
    page = client.get(f"/evidence/{v1}")

    assert b"Version 3" in client.get(f"/evidence/{v3}").data
    for eid in (v1, v2, v3):
        assert f'href="/evidence/{eid}"'.encode() in page.data


def test_upload_version_of_other_users_evidence_is_rejected(client):
    signup_and_login(client, "alice")
    alice_eid = evidence_id_from(upload(client))
    logout(client)
    signup_and_login(client, "mallory")

    response = upload(client, b"fake", parent_id=str(alice_eid))

    assert response.status_code == 403


def test_dashboard_search_and_status_filter_return_only_matching_evidence(app, client, chain):
    signup_and_login(client, "alice")
    upload(client, b"a", title="Poster artwork")
    upload(client, b"b", title="Source code")
    anchored = evidence_id_from(upload(client, b"c", title="Poster final"))
    approve(app, anchored)
    post(client, f"/evidence/{anchored}/anchor/server")

    by_text = client.get("/dashboard?q=poster").data
    by_status = client.get("/dashboard?q=poster&status=confirmed").data

    assert b"Poster artwork" in by_text and b"Poster final" in by_text and b"Source code" not in by_text
    assert b"Poster final" in by_status and b"Poster artwork" not in by_status
