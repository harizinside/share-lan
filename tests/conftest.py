"""Fixture bersama: bikin folder contoh dan nyalain server beneran di port acak."""

import http.client
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lanshare as L

L.log = lambda *a, **k: None  # log server nggak perlu berisik di output tes


@pytest.fixture
def tree(tmp_path):
    """Folder contoh: campur file, subfolder, unicode, dotfile, basename kembar."""
    (tmp_path / "Dokumen" / "arsip").mkdir(parents=True)
    (tmp_path / "Galeri").mkdir()
    (tmp_path / "lain").mkdir()
    (tmp_path / "Dokumen" / "catatan.txt").write_bytes(b"halo dunia\n" * 50)
    (tmp_path / "Dokumen" / "data.bin").write_bytes(bytes(range(256)) * 400)
    (tmp_path / "Dokumen" / "arsip" / "kopi ☕.txt").write_text("unicode ok")
    (tmp_path / "Dokumen" / ".rahasia").write_text("jangan ikut")
    (tmp_path / "lain" / "catatan.txt").write_text("versi lain")
    (tmp_path / "klip.mp4").write_bytes(b"\x00\x01\x02\x03" * 5000)
    if L.HAVE_PIL:
        from PIL import Image

        Image.new("RGB", (400, 300), (200, 80, 90)).save(tmp_path / "Galeri" / "foto.jpg")
    return tmp_path


class Client:
    """Klien HTTP kecil biar bisa ngatur header dan cookie sendiri."""

    def __init__(self, port):
        self.port = port
        self.cookie = None

    def request(self, method, path, body=None, headers=None, use_cookie=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        head = dict(headers or {})
        if use_cookie and self.cookie:
            head["Cookie"] = self.cookie
        if body is not None:
            head.setdefault("Content-Length", str(len(body)))
        conn.request(method, path, body=body, headers=head)
        resp = conn.getresponse()
        data = resp.read()
        set_cookie = resp.getheader("Set-Cookie")
        if set_cookie:
            self.cookie = set_cookie.split(";")[0]
        conn.close()
        return resp.status, dict(resp.getheaders()), data

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def login(self, code):
        return self.request(
            "POST",
            "/login",
            body=f"code={code}".encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )


def start(tmp_paths, extra=None):
    """Nyalain server di port acak dengan konfigurasi tertentu."""
    argv = [str(p) for p in tmp_paths] + ["--code", "4815", "--no-qr"] + (extra or [])
    cfg = L.parse_args(argv)
    cfg.port = 0
    L.ST.fails.clear()
    L.ST.sessions.clear()
    L.ST.zip_plans.clear()
    L.ST.global_fails = 0
    L.configure(cfg)
    httpd = L.Server(("127.0.0.1", 0), L.Handler)
    port = httpd.server_address[1]
    L.ST.base_url = f"http://127.0.0.1:{port}"
    # poll cepet: shutdown() default nunggu 0.5 detik per server, kali puluhan tes
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    return httpd, Client(port)


@pytest.fixture
def server(tree):
    httpd, client = start(
        [tree / "Dokumen", tree / "Galeri", tree / "klip.mp4"], ["--upload-to", str(tree / "lain")]
    )
    yield client
    httpd.shutdown()
    httpd.server_close()
