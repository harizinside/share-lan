"""Shared fixtures: build a sample folder tree and start a real server on a random port."""

import http.client
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lanshare as L

L.fmt.log = lambda *a, **k: None  # the server's logging doesn't need to be noisy in test output


@pytest.fixture
def tree(tmp_path):
    """Sample folder tree: mixed files, subfolders, unicode, a dotfile, duplicate basenames."""
    (tmp_path / "Documents" / "archive").mkdir(parents=True)
    (tmp_path / "Gallery").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "Documents" / "notes.txt").write_bytes(b"hello world\n" * 50)
    (tmp_path / "Documents" / "data.bin").write_bytes(bytes(range(256)) * 400)
    (tmp_path / "Documents" / "archive" / "coffee ☕.txt").write_text("unicode ok")
    (tmp_path / "Documents" / ".secret").write_text("do not include")
    (tmp_path / "other" / "notes.txt").write_text("other version")
    (tmp_path / "clip.mp4").write_bytes(b"\x00\x01\x02\x03" * 5000)
    if L.HAVE_PIL:
        from PIL import Image

        Image.new("RGB", (400, 300), (200, 80, 90)).save(tmp_path / "Gallery" / "photo.jpg")
    return tmp_path


class Client:
    """A small HTTP client so tests can control their own headers and cookies."""

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
    """Start a server on a random port with a given configuration."""
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
    # fast polling: shutdown() otherwise waits up to 0.5s per server, times dozens of tests
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    return httpd, Client(port)


@pytest.fixture
def server(tree):
    httpd, client = start(
        [tree / "Documents", tree / "Gallery", tree / "other", tree / "clip.mp4"], []
    )
    yield client
    httpd.shutdown()
    httpd.server_close()
