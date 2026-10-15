"""Document ingestion: upload -> store -> chunk -> embed -> retrievable (tenant-scoped)."""

import pytest

from tests.conftest import auth

pytestmark = pytest.mark.integration

DOC = b"""# Acme Winterization Procedure

## E-HTR-220: Heat strip interlock open

The auxiliary heat strip interlock opened because the airflow switch did not close.

Resolution steps:

1. Replace the clogged return filter.
2. Verify the blower belt tension.
3. Reset the interlock and confirm airflow switch closure.
"""


async def test_upload_indexes_document_and_makes_it_searchable(api, tokens):
    files = {"file": ("winterization.md", DOC, "text/markdown")}
    resp = await api.post("/api/tenants/acme/documents", headers=auth(tokens["engineer"]), files=files)
    assert resp.status_code == 202, resp.text
    doc = resp.json()
    assert doc["status"] == "indexed" and doc["chunk_count"] >= 1  # INGEST_MODE=inline in tests

    hits = (
        await api.get(
            "/api/tenants/acme/knowledge/search",
            params={"q": "E-HTR-220 heat strip interlock"},
            headers=auth(tokens["engineer"]),
        )
    ).json()
    assert hits[0]["document"] == "winterization.md" and "Heat strip interlock" in hits[0]["content"]

    other = (
        await api.get(
            "/api/tenants/betacorp/knowledge/search",
            params={"q": "E-HTR-220 heat strip interlock"},
            headers=auth(tokens["engineer"]),
        )
    ).json()
    assert all(h["document"] != "winterization.md" for h in other)


async def test_rejects_unsupported_and_empty_files(api, tokens):
    bad = await api.post(
        "/api/tenants/acme/documents",
        headers=auth(tokens["engineer"]),
        files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
    )
    assert bad.status_code == 415
    empty = await api.post(
        "/api/tenants/acme/documents",
        headers=auth(tokens["engineer"]),
        files={"file": ("empty.md", b"", "text/markdown")},
    )
    assert empty.status_code == 422


async def test_viewer_cannot_upload(api, tokens):
    resp = await api.post(
        "/api/tenants/acme/documents",
        headers=auth(tokens["viewer"]),
        files={"file": ("x.md", b"# hi", "text/markdown")},
    )
    assert resp.status_code == 403
