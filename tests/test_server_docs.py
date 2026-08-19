"""
ET /api/docs/manifest_guide: the wrapped page for direct visits and
the ``?raw=1`` markdown text the in-overlay Settings manual fetches.
"""

from fastapi.testclient import TestClient

from dlc.web.server import app

client = TestClient(app)


def test_manifest_guide_serves_wrapped_page_and_raw_markdown():
    wrapped = client.get("/api/docs/manifest_guide")
    assert wrapped.status_code == 200
    assert wrapped.headers["content-type"].startswith("text/html")
    assert "<pre" in wrapped.text

    raw = client.get("/api/docs/manifest_guide", params={"raw": 1})
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("text/plain")
    assert raw.text.lstrip().startswith("# Configuring DLC")
    assert "<pre" not in raw.text
