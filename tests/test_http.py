"""Tes end-to-end lewat HTTP beneran: auth, alur scan, Range, ZIP, upload."""

import hashlib
import io
import zipfile

import pytest
from conftest import start

import lanshare as L

# ---------------------------------------------------------------- auth & scan


def test_tanpa_kode_ditolak(server):
    status, _, _ = server.get("/api/list")
    assert status == 401


def test_halaman_utama_dilempar_ke_login(server):
    status, headers, _ = server.get("/")
    assert status == 302
    assert "/login" in headers["Location"]


def test_scan_langsung_download_tanpa_cookie(server):
    """Inti alur QR: kode di URL harus langsung ngirim file dalam satu request."""
    status, _, body = server.get("/dl?p=klip.mp4&code=4815", use_cookie=False)
    assert status == 200
    assert len(body) == 20000


def test_scan_kode_salah_dilempar_ke_login(server):
    status, headers, _ = server.get("/dl?p=klip.mp4&code=9999", use_cookie=False)
    assert status == 302
    assert "/login" in headers["Location"]


def test_login_ngasih_cookie_dan_akses(server):
    status, headers, _ = server.login("4815")
    assert status == 302
    assert "HttpOnly" in headers["Set-Cookie"]
    assert server.get("/api/list")[0] == 200


def test_kode_di_url_dibuang_dari_halaman_utama(server):
    """Biar kode nggak nyangkut di history HP."""
    status, headers, _ = server.get("/?code=4815", use_cookie=False)
    assert status == 302
    assert "code" not in headers["Location"]


def test_rate_limit_ngunci_setelah_lima_kali_salah(server):
    for _ in range(5):
        assert server.login("0000")[0] == 401
    # kode yang bener pun ditolak selama masih dikunci
    assert server.login("4815")[0] == 429


# ---------------------------------------------------------------- listing


def test_listing_root_nampilin_mount(server):
    server.login("4815")
    import json

    data = json.loads(server.get("/api/list")[1] and server.get("/api/list")[2])
    assert [e["name"] for e in data["entries"]] == ["Dokumen", "Galeri", "klip.mp4"]
    assert data["parent"] is None


def test_listing_gak_bocorin_path_asli(server, tree):
    server.login("4815")
    _, _, body = server.get("/api/list")
    assert str(tree).encode() not in body


# ---------------------------------------------------------------- Range


def test_range_ngasih_206(server):
    server.login("4815")
    status, headers, body = server.get("/dl?p=klip.mp4", headers={"Range": "bytes=0-99"})
    assert status == 206
    assert headers["Content-Range"] == "bytes 0-99/20000"
    assert body == (b"\x00\x01\x02\x03" * 5000)[:100]


def test_range_di_luar_ukuran_ngasih_416(server):
    server.login("4815")
    status, headers, _ = server.get("/dl?p=klip.mp4", headers={"Range": "bytes=99999-"})
    assert status == 416
    assert headers["Content-Range"] == "bytes */20000"


def test_multi_range_dibales_penuh(server):
    server.login("4815")
    status, _, body = server.get("/dl?p=klip.mp4", headers={"Range": "bytes=0-9,20-29"})
    assert status == 200
    assert len(body) == 20000


def test_download_ngiklanin_accept_ranges(server):
    server.login("4815")
    _, headers, _ = server.get("/dl?p=klip.mp4")
    assert headers["Accept-Ranges"] == "bytes"


def test_head_gak_ngirim_body(server):
    server.login("4815")
    status, headers, body = server.request("HEAD", "/dl?p=klip.mp4")
    assert status == 200
    assert headers["Content-Length"] == "20000"
    assert body == b""


# ---------------------------------------------------------------- ZIP


def test_zip_kecil_bisa_diresume(server):
    server.login("4815")
    status, headers, utuh = server.get("/zip?p=Dokumen")
    assert status == 200
    assert headers["Accept-Ranges"] == "bytes"
    assert int(headers["Content-Length"]) == len(utuh)

    potong = len(utuh) // 3
    _, _, sisa = server.get("/zip?p=Dokumen", headers={"Range": f"bytes={potong}-"})
    gabungan = utuh[:potong] + sisa
    assert gabungan == utuh
    assert zipfile.ZipFile(io.BytesIO(gabungan)).testzip() is None


def test_zip_semua_mount(server):
    server.login("4815")
    _, _, body = server.get("/zip?p=")
    names = zipfile.ZipFile(io.BytesIO(body)).namelist()
    assert any(n.startswith("Dokumen/") for n in names)
    assert "klip.mp4" in names


def test_zip_gede_pindah_ke_streaming(tree):
    httpd, client = start([tree / "Dokumen"], ["--zip-resume-limit", "1K"])
    try:
        client.login("4815")
        status, headers, body = client.get("/zip?p=Dokumen")
        assert status == 200
        assert "Content-Length" not in headers  # ukuran belum ketauan -> nggak bisa resume
        assert headers.get("Connection") == "close"
        assert zipfile.ZipFile(io.BytesIO(body)).testzip() is None
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_zipinfo_bilang_bisa_resume_atau_nggak(server):
    import json

    server.login("4815")
    info = json.loads(server.get("/api/zipinfo?p=Dokumen")[2])
    assert info["resumable"] is True
    assert info["count"] == 3


# ---------------------------------------------------------------- upload


def test_upload_masuk_dan_nama_bentrok_diberi_nomor(server, tree):
    server.login("4815")
    for expected in ("kirim.bin", "kirim (2).bin"):
        status, _, body = server.request("PUT", "/up?name=kirim.bin", body=b"x" * 100)
        assert status == 200
        assert expected.encode() in body
    assert (tree / "lain" / "kirim (2).bin").exists()


def test_upload_nama_jahat_gak_nembus_keluar(server, tree):
    server.login("4815")
    server.request("PUT", "/up?name=..%2F..%2Fevil.sh", body=b"x")
    assert (tree / "lain" / "evil.sh").exists()
    assert not (tree / "evil.sh").exists()
    assert not (tree.parent / "evil.sh").exists()


def test_upload_butuh_content_length(server):
    server.login("4815")
    status, _, _ = server.request("PUT", "/up?name=a.bin", headers={"Transfer-Encoding": "chunked"})
    assert status == 411


def test_read_only_nolak_upload(tree):
    httpd, client = start([tree / "Dokumen"], ["--read-only"])
    try:
        client.login("4815")
        assert client.request("PUT", "/up?name=a.bin", body=b"x")[0] == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_max_upload_ditegakkan(tree):
    httpd, client = start([tree / "Dokumen"], ["--max-upload", "10"])
    try:
        client.login("4815")
        assert client.request("PUT", "/up?name=a.bin", body=b"x" * 100)[0] == 413
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------- lain-lain


def test_traversal_ditolak_di_semua_route(server):
    server.login("4815")
    for route in ("/dl", "/zip", "/api/hash", "/thumb", "/urls", "/sums"):
        status, _, _ = server.get(f"{route}?p=Dokumen%2F..%2F..%2Fetc%2Fpasswd")
        assert status in (403, 404), route


def test_sums_cocok_sama_hashlib(server, tree):
    server.login("4815")
    _, _, body = server.get("/sums?p=Dokumen")
    isi = dict(reversed(b.split("  ", 1)) for b in body.decode().strip().split("\n"))
    asli = hashlib.sha256((tree / "Dokumen" / "catatan.txt").read_bytes()).hexdigest()
    assert isi["catatan.txt"] == asli


def test_urls_bawa_kode_biar_bisa_wget(server):
    server.login("4815")
    _, _, body = server.get("/urls?p=Dokumen")
    baris = body.decode().strip().split("\n")
    assert len(baris) == 3
    assert all("code=4815" in b and "/dl?p=" in b for b in baris)


def test_qr_ngasih_svg(server):
    server.login("4815")
    status, headers, body = server.get("/qr?u=http://contoh")
    assert status == 200
    assert "svg" in headers["Content-Type"]
    assert body.startswith(b"<svg")


@pytest.mark.skipif(not L.HAVE_PIL, reason="Pillow nggak kepasang")
def test_thumbnail_gambar(server):
    server.login("4815")
    status, headers, body = server.get("/thumb?p=Galeri/foto.jpg")
    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert len(body) < 60000  # jauh lebih kecil dari aslinya


def test_thumbnail_bukan_gambar_ngasih_404(server):
    server.login("4815")
    assert server.get("/thumb?p=klip.mp4")[0] == 404


def test_server_kosong_gak_ngebocorin_apa_apa(tree):
    """Jalan tanpa path: server nyala, tapi nol file kebuka."""
    import json

    httpd, client = start([], [])
    try:
        client.login("4815")
        data = json.loads(client.get("/api/list")[2])
        assert data["entries"] == []
        assert data["shared_count"] == 0
        assert data["can_upload"] is False
        assert client.get("/dl?p=apa-aja.txt")[0] == 404
        assert client.get("/zip?p=")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_file_yang_ditambah_langsung_keliatan(tree):
    """Host lempar path sambil server jalan -> penerima langsung bisa lihat & download,
    dan penanda revisi naik biar halaman yang lagi kebuka ikut nyegerin sendiri."""
    import json

    httpd, client = start([], [])
    try:
        client.login("4815")
        rev_awal = json.loads(client.get("/api/rev")[2])["rev"]

        L.add_paths([str(tree / "klip.mp4")])

        assert json.loads(client.get("/api/rev")[2])["rev"] > rev_awal
        data = json.loads(client.get("/api/list")[2])
        assert [e["name"] for e in data["entries"]] == ["klip.mp4"]
        status, _, body = client.get("/dl?p=klip.mp4")
        assert status == 200
        assert len(body) == 20000
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_yang_dicabut_langsung_gak_bisa_diakses(tree):
    httpd, client = start([tree / "Dokumen", tree / "klip.mp4"], [])
    try:
        client.login("4815")
        assert client.get("/dl?p=klip.mp4")[0] == 200
        L.remove_mount("klip.mp4")
        assert client.get("/dl?p=klip.mp4")[0] == 404
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_download_gak_nyalin_file_ke_mana_pun(tree, tmp_path, monkeypatch):
    """Server cuma megang path. Nggak ada file yang nyasar ke folder kerja."""
    kerja = tmp_path / "folder-kerja"
    kerja.mkdir()
    monkeypatch.chdir(kerja)
    httpd, client = start([tree / "klip.mp4"], [])
    try:
        client.login("4815")
        assert len(client.get("/dl?p=klip.mp4")[2]) == 20000
        client.get("/zip?p=")
        assert list(kerja.iterdir()) == []  # nol jejak
        assert L.ST.mounts["klip.mp4"] == str(tree / "klip.mp4")  # path asli, bukan salinan
    finally:
        httpd.shutdown()
        httpd.server_close()
