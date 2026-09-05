"""End-to-end tests over real HTTP: auth, the scan flow, Range, ZIP, upload."""

import hashlib
import io
import zipfile

import pytest
from conftest import start

import lanshare as L

# ---------------------------------------------------------------- auth & scan


def test_no_code_is_rejected(server):
    status, _, _ = server.get("/api/list")
    assert status == 401


def test_main_page_redirects_to_login(server):
    status, headers, _ = server.get("/")
    assert status == 302
    assert "/login" in headers["Location"]


def test_scan_downloads_directly_without_cookie(server):
    """The core of the QR flow: a code in the URL must send the file in a single request."""
    status, _, body = server.get("/dl?p=clip.mp4&code=4815", use_cookie=False)
    assert status == 200
    assert len(body) == 20000


def test_wrong_code_scan_redirects_to_login(server):
    status, headers, _ = server.get("/dl?p=clip.mp4&code=9999", use_cookie=False)
    assert status == 302
    assert "/login" in headers["Location"]


def test_login_grants_cookie_and_access(server):
    status, headers, _ = server.login("4815")
    assert status == 302
    assert "HttpOnly" in headers["Set-Cookie"]
    assert server.get("/api/list")[0] == 200


def test_url_code_stripped_from_main_page(server):
    """So the code doesn't linger in a phone's browser history."""
    status, headers, _ = server.get("/?code=4815", use_cookie=False)
    assert status == 302
    assert "code" not in headers["Location"]


def test_rate_limit_locks_after_five_failures(server):
    for _ in range(5):
        assert server.login("0000")[0] == 401
    # even the correct code is rejected while still locked
    assert server.login("4815")[0] == 429


# ---------------------------------------------------------------- listing


def test_root_listing_shows_mounts(server):
    server.login("4815")
    import json

    data = json.loads(server.get("/api/list")[1] and server.get("/api/list")[2])
    assert [e["name"] for e in data["entries"]] == ["Documents", "Gallery", "other", "clip.mp4"]
    assert data["parent"] is None


def test_listing_does_not_leak_real_path(server, tree):
    server.login("4815")
    _, _, body = server.get("/api/list")
    assert str(tree).encode() not in body


# ---------------------------------------------------------------- Range


def test_range_returns_206(server):
    server.login("4815")
    status, headers, body = server.get("/dl?p=clip.mp4", headers={"Range": "bytes=0-99"})
    assert status == 206
    assert headers["Content-Range"] == "bytes 0-99/20000"
    assert body == (b"\x00\x01\x02\x03" * 5000)[:100]


def test_range_out_of_bounds_returns_416(server):
    server.login("4815")
    status, headers, _ = server.get("/dl?p=clip.mp4", headers={"Range": "bytes=99999-"})
    assert status == 416
    assert headers["Content-Range"] == "bytes */20000"


def test_multi_range_returns_full_body(server):
    server.login("4815")
    status, _, body = server.get("/dl?p=clip.mp4", headers={"Range": "bytes=0-9,20-29"})
    assert status == 200
    assert len(body) == 20000


def test_download_advertises_accept_ranges(server):
    server.login("4815")
    _, headers, _ = server.get("/dl?p=clip.mp4")
    assert headers["Accept-Ranges"] == "bytes"


def test_head_sends_no_body(server):
    server.login("4815")
    status, headers, body = server.request("HEAD", "/dl?p=clip.mp4")
    assert status == 200
    assert headers["Content-Length"] == "20000"
    assert body == b""


# ---------------------------------------------------------------- ZIP


def test_small_zip_is_resumable(server):
    server.login("4815")
    status, headers, whole = server.get("/zip?p=Documents")
    assert status == 200
    assert headers["Accept-Ranges"] == "bytes"
    assert int(headers["Content-Length"]) == len(whole)

    cut = len(whole) // 3
    _, _, rest = server.get("/zip?p=Documents", headers={"Range": f"bytes={cut}-"})
    joined = whole[:cut] + rest
    assert joined == whole
    assert zipfile.ZipFile(io.BytesIO(joined)).testzip() is None


def test_zip_all_mounts(server):
    server.login("4815")
    _, _, body = server.get("/zip?p=")
    names = zipfile.ZipFile(io.BytesIO(body)).namelist()
    assert any(n.startswith("Documents/") for n in names)
    assert "clip.mp4" in names


def test_zip_selection_multiple_items(server):
    server.login("4815")
    # a selection: the Documents folder + the clip.mp4 file (sent as repeated p=)
    _, _, body = server.get("/zip?p=Documents&p=clip.mp4")
    zf = zipfile.ZipFile(io.BytesIO(body))
    names = zf.namelist()
    assert zf.testzip() is None
    assert any(n.startswith("Documents/") for n in names)
    assert "clip.mp4" in names
    # each item stays rooted in its own archive top-level (not dumped together)
    assert not any(n.startswith("notes.txt") for n in names)


def test_large_zip_falls_back_to_streaming(tree):
    httpd, client = start([tree / "Documents"], ["--zip-resume-limit", "1K"])
    try:
        client.login("4815")
        status, headers, body = client.get("/zip?p=Documents")
        assert status == 200
        assert "Content-Length" not in headers  # size isn't known upfront -> can't resume
        assert headers.get("Connection") == "close"
        assert zipfile.ZipFile(io.BytesIO(body)).testzip() is None
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_zipinfo_reports_resumability(server):
    import json

    server.login("4815")
    info = json.loads(server.get("/api/zipinfo?p=Documents")[2])
    assert info["resumable"] is True
    assert info["count"] == 3


# ---------------------------------------------------------------- upload


def test_upload_succeeds_and_dedupes_name(server, tree):
    server.login("4815")
    for expected in ("send.bin", "send (2).bin"):
        status, _, body = server.request("PUT", "/up?name=send.bin&p=other", body=b"x" * 100)
        assert status == 200
        assert expected.encode() in body
    assert (tree / "other" / "send (2).bin").exists()


def test_upload_malicious_name_is_contained(server, tree):
    server.login("4815")
    server.request("PUT", "/up?name=..%2F..%2Fevil.sh&p=other", body=b"x")
    assert (tree / "other" / "evil.sh").exists()
    assert not (tree / "evil.sh").exists()
    assert not (tree.parent / "evil.sh").exists()


def test_upload_requires_content_length(server):
    server.login("4815")
    status, _, _ = server.request(
        "PUT", "/up?name=a.bin&p=other", headers={"Transfer-Encoding": "chunked"}
    )
    assert status == 411


def test_read_only_rejects_upload(tree):
    httpd, client = start([tree / "Documents"], ["--read-only"])
    try:
        client.login("4815")
        assert client.request("PUT", "/up?name=a.bin&p=Documents", body=b"x")[0] == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_max_upload_is_enforced(tree):
    httpd, client = start([tree / "Documents"], ["--max-upload", "10"])
    try:
        client.login("4815")
        assert client.request("PUT", "/up?name=a.bin&p=Documents", body=b"x" * 100)[0] == 413
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_upload_follows_open_subfolder(server, tree):
    """The core of this bug fix: uploads must land in the open subfolder, not the root."""
    server.login("4815")
    status, _, _ = server.request("PUT", "/up?name=sub.bin&p=Documents/archive", body=b"y" * 100)
    assert status == 200
    assert (tree / "Documents" / "archive" / "sub.bin").exists()
    assert not (tree / "sub.bin").exists()
    assert not (tree / "Documents" / "sub.bin").exists()


def test_upload_without_folder_or_virtual_root_rejected(server, tree):
    """The server fixture has more than one mount -> a virtual root. Without `p` there's
    no clear target folder, so it must be rejected."""
    server.login("4815")
    assert server.request("PUT", "/up?name=a.bin", body=b"x")[0] == 404
    assert server.request("PUT", "/up?name=a.bin&p=", body=b"x")[0] == 404


def test_upload_escaping_mount_rejected(server):
    server.login("4815")
    status, _, _ = server.request("PUT", "/up?name=a.bin&p=Documents/..%2F..%2Fetc", body=b"x")
    assert status == 403


def test_upload_to_file_mount_rejected(server):
    """A mount that's just a single file (not a folder) can't be an upload target."""
    server.login("4815")
    assert server.request("PUT", "/up?name=a.bin&p=clip.mp4", body=b"x")[0] == 404


def test_upload_target_pointing_to_file_rejected(server):
    """`p` points to a file inside a folder -> not a folder, rejected."""
    server.login("4815")
    assert server.request("PUT", "/up?name=a.bin&p=Documents/notes.txt", body=b"x")[0] == 404


def test_can_upload_follows_folder_writability(tree):
    """can_upload varies per folder: a writable folder allows it, an unwritable one doesn't."""
    import json

    read_only_dir = tree / "Documents" / "read-only"
    read_only_dir.mkdir()
    httpd, client = start([tree / "Documents"], [])
    try:
        client.login("4815")
        assert json.loads(client.get("/api/list?p=Documents")[2])["can_upload"] is True
        try:
            read_only_dir.chmod(0o555)
            cannot_upload = json.loads(client.get("/api/list?p=Documents/read-only")[2])[
                "can_upload"
            ]
        finally:
            read_only_dir.chmod(0o755)
        import os

        if os.geteuid() != 0:  # root bypasses permissions -> let it be flaky
            assert cannot_upload is False
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------- misc


def test_traversal_rejected_on_all_routes(server):
    server.login("4815")
    for route in ("/dl", "/zip", "/api/hash", "/thumb", "/preview", "/urls", "/sums"):
        status, _, _ = server.get(f"{route}?p=Documents%2F..%2F..%2Fetc%2Fpasswd")
        assert status in (403, 404), route


def test_sums_match_hashlib(server, tree):
    server.login("4815")
    _, _, body = server.get("/sums?p=Documents")
    contents = dict(reversed(b.split("  ", 1)) for b in body.decode().strip().split("\n"))
    original = hashlib.sha256((tree / "Documents" / "notes.txt").read_bytes()).hexdigest()
    assert contents["notes.txt"] == original


def test_urls_include_code_for_wget(server):
    server.login("4815")
    _, _, body = server.get("/urls?p=Documents")
    lines = body.decode().strip().split("\n")
    assert len(lines) == 3
    assert all("code=4815" in b and "/dl?p=" in b for b in lines)


def test_qr_returns_svg(server):
    server.login("4815")
    status, headers, body = server.get("/qr?u=http://example")
    assert status == 200
    assert "svg" in headers["Content-Type"]
    assert body.startswith(b"<svg")


@pytest.mark.skipif(not L.HAVE_PIL, reason="Pillow not installed")
def test_thumbnail_image(server):
    server.login("4815")
    status, headers, body = server.get("/thumb?p=Gallery/photo.jpg")
    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert len(body) < 60000  # far smaller than the original


@pytest.mark.skipif(not L.HAVE_PIL, reason="Pillow not installed")
def test_thumbnail_svg_sent_as_is(server, tree):
    (tree / "Gallery" / "logo.svg").write_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
        b'<rect width="80" height="40" fill="#397"/></svg>'
    )
    server.login("4815")
    status, headers, body = server.get("/thumb?p=Gallery/logo.svg")
    assert status == 200
    assert headers["Content-Type"] == "image/svg+xml"
    assert body.startswith(b"<svg")
    assert server.get("/thumb?p=Gallery/logo.svg")[2] == body  # hits the cache


def test_thumbnail_non_image_returns_404(server):
    server.login("4815")
    assert server.get("/thumb?p=clip.mp4")[0] == 404


def test_thumbnail_pdf_top_half_of_first_page(server, tree):
    from PIL import Image, ImageDraw

    path = tree / "Documents" / "preview.PDF"
    first = Image.new("RGB", (400, 600), "red")
    ImageDraw.Draw(first).rectangle((0, 300, 399, 599), fill="blue")
    second = Image.new("RGB", (400, 600), "green")
    first.save(path, "PDF", save_all=True, append_images=[second])
    assert server.get("/thumb?p=Documents/preview.PDF")[0] == 302
    server.login("4815")
    status, headers, body = server.get("/thumb?p=Documents/preview.PDF")
    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    with Image.open(io.BytesIO(body)) as preview:
        assert preview.size == (360, 270)
        for xy in ((180, 10), (180, 260)):
            r, g, b = preview.getpixel(xy)
            assert r > 200 and g < 30 and b < 30
    assert server.get("/thumb?p=Documents/preview.PDF")[2] == body


def test_thumbnail_corrupt_pdf_returns_404(server, tree):
    (tree / "Documents" / "corrupt.pdf").write_bytes(b"not a PDF")
    server.login("4815")
    assert server.get("/thumb?p=Documents/corrupt.pdf")[0] == 404


# ---------------------------------------------------------------- preview


def test_main_page_has_preview_modal(server):
    server.login("4815")
    _, _, body = server.get("/")
    html = body.decode()
    assert 'id="pvModal"' in html
    assert "canPreview" in html  # the preview button is rendered by this JS


def test_preview_text_inline(server):
    server.login("4815")
    status, headers, body = server.get("/preview?p=Documents/notes.txt")
    assert status == 200
    assert headers["Content-Type"].startswith("text/plain")
    assert b"hello world" in body


@pytest.mark.skipif(L.HAVE_GROUPDOCS, reason="GroupDocs installed, Office formats do render")
def test_preview_office_without_groupdocs_returns_404(server, tree):
    (tree / "Documents" / "report.docx").write_bytes(b"not really a docx")
    server.login("4815")
    status, _, body = server.get("/preview?p=Documents/report.docx")
    assert status == 404
    assert b"groupdocs-viewer-net" in body


def test_preview_unknown_format_returns_404(server):
    server.login("4815")
    assert server.get("/preview?p=Documents/data.bin")[0] == 404


def test_preview_pdf_sent_inline(server, tree):
    from PIL import Image

    path = tree / "Documents" / "mini.pdf"
    Image.new("RGB", (200, 300), "white").save(path, "PDF")
    server.login("4815")
    status, headers, body = server.get("/preview?p=Documents/mini.pdf")
    assert status == 200
    assert body.startswith(b"%PDF")
    assert "inline" in headers["Content-Disposition"]


def test_empty_server_leaks_nothing(tree):
    """Running with no paths: the server starts, but zero files are open."""
    import json

    httpd, client = start([], [])
    try:
        client.login("4815")
        data = json.loads(client.get("/api/list")[2])
        assert data["entries"] == []
        assert data["shared_count"] == 0
        assert data["can_upload"] is False
        assert client.get("/dl?p=anything.txt")[0] == 404
        assert client.get("/zip?p=")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_added_file_is_immediately_visible(tree):
    """The host drops a path while the server runs -> recipients can see & download it right
    away, and the revision counter bumps so an already-open page refreshes itself."""
    import json

    httpd, client = start([], [])
    try:
        client.login("4815")
        rev_start = json.loads(client.get("/api/rev")[2])["rev"]

        L.add_paths([str(tree / "clip.mp4")])

        assert json.loads(client.get("/api/rev")[2])["rev"] > rev_start
        data = json.loads(client.get("/api/list")[2])
        assert [e["name"] for e in data["entries"]] == ["clip.mp4"]
        status, _, body = client.get("/dl?p=clip.mp4")
        assert status == 200
        assert len(body) == 20000
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_removed_mount_immediately_inaccessible(tree):
    httpd, client = start([tree / "Documents", tree / "clip.mp4"], [])
    try:
        client.login("4815")
        assert client.get("/dl?p=clip.mp4")[0] == 200
        L.remove_mount("clip.mp4")
        assert client.get("/dl?p=clip.mp4")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_download_does_not_copy_file_anywhere(tree, tmp_path, monkeypatch):
    """The server only ever holds a path. No file ends up in the working folder."""
    work_dir = tmp_path / "work-folder"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)
    httpd, client = start([tree / "clip.mp4"], [])
    try:
        client.login("4815")
        assert len(client.get("/dl?p=clip.mp4")[2]) == 20000
        client.get("/zip?p=")
        assert list(work_dir.iterdir()) == []  # zero footprint
        assert L.ST.mounts["clip.mp4"] == str(tree / "clip.mp4")  # the real path, not a copy
    finally:
        httpd.shutdown()
        httpd.server_close()
