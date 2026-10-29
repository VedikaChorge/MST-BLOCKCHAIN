import pytest


@pytest.mark.parametrize("path,heading", [
    ("/terms", b"Terms &amp; Conditions"),
    ("/privacy", b"Privacy Policy"),
    ("/refunds", b"Refund &amp; Cancellation Policy"),
    ("/contact", b"Contact us"),
])
def test_policy_pages_are_public_and_show_business_details(app, client, monkeypatch, path, heading):
    monkeypatch.setitem(app.BUSINESS, "name", "Priya Studio Pvt Ltd")
    monkeypatch.setitem(app.BUSINESS, "email", "support@priya.example")

    page = client.get(path)

    assert page.status_code == 200
    assert heading in page.data
    assert b"Priya Studio Pvt Ltd" in page.data
    assert b"support@priya.example" in page.data


def test_footer_links_to_all_policy_pages(client):
    page = client.get("/").data

    for path in (b"/terms", b"/privacy", b"/refunds", b"/contact"):
        assert b'href="' + path + b'"' in page
