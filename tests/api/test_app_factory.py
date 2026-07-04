from __future__ import annotations


def test_create_app_exposes_expected_routes_and_static_assets(client):
    index = client.get("/")
    assert index.status_code == 200
    assert 'type="module"' in index.text
    assert "./js/app.js" in index.text

    styles = client.get("/styles.css")
    assert styles.status_code == 200
    assert "font-family" in styles.text

    app_js = client.get("/js/app.js")
    assert app_js.status_code == 200
    assert "testModule" in app_js.text
