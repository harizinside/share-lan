import html
import json
import mimetypes
import os
import shutil
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import fmt
from .auth import check_code, lock_left, new_session, valid_session
from .fmt import C, human
from .mounts import (
    Denied,
    Missing,
    can_upload_here,
    list_entries,
    resolve,
    safe_upload_name,
    total_stats,
    walk_files,
)
from .pages import ERROR_PAGE, login_page, page_html
from .preview import DOC_EXT, HAVE_GROUPDOCS, RenderError, previewable, render_html
from .qr import qr_svg
from .state import APP, CHUNK, INLINE_EXT, SESSION_TTL, ST
from .thumb import make_thumb, sha256_of
from .ziputil import Stale, StreamWriter, build_zip_plan, emit_plan, zip_entries, zip_name


def parse_range(header, size):
    if not header or not header.strip().startswith("bytes="):
        return None
    spec = header.strip()[6:].strip()
    if "," in spec:
        return "full"  # multi-range: sah dibales isi penuh, browser nggak pernah minta ini
    lo_s, _, hi_s = spec.partition("-")
    try:
        if lo_s == "":
            n = int(hi_s)
            if n <= 0:
                return "full"
            lo, hi = max(0, size - n), size - 1
        else:
            lo = int(lo_s)
            hi = int(hi_s) if hi_s else size - 1
    except ValueError:
        return "full"
    if lo < 0 or lo >= size:
        return "unsat"
    hi = min(hi, size - 1)
    if hi < lo:
        return "full"
    return (lo, hi)


def unique_path(folder, name):
    dest = os.path.join(folder, name)
    if not os.path.exists(dest):
        return dest
    stem, ext = os.path.splitext(name)
    i = 2
    while os.path.exists(os.path.join(folder, f"{stem} ({i}){ext}")):
        i += 1
    return os.path.join(folder, f"{stem} ({i}){ext}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = APP
    sys_version = ""
    timeout = 300

    # -- dasar ------------------------------------------------------------
    def log_message(self, *a):
        pass

    def log_request(self, *a):
        pass

    @property
    def ip(self):
        return self.client_address[0]

    def qget(self, qs, key, default=""):
        v = qs.get(key)
        return v[0] if v else default

    def cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == APP:
                return v
        return ""

    def authed(self, qs):
        if valid_session(self.cookie_token()):
            return True
        code = self.qget(qs, "code")
        if code and check_code(self.ip, code):
            self.pending_cookie = new_session()
            return True
        return False

    def head(self, status, ctype=None, length=None, extra=None, close=False):
        self.send_response(status)
        if ctype:
            self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        if getattr(self, "pending_cookie", None):
            self.send_header(
                "Set-Cookie",
                f"{APP}={self.pending_cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}",
            )
            self.pending_cookie = None
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()

    def send(self, status, body=b"", ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.head(status, ctype, len(body), extra)
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def send_json(self, obj, status=200):
        self.send(status, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def fail(self, status, msg):
        if self.path.startswith("/api/"):
            self.send_json({"error": msg}, status)
        else:
            self.send(
                status,
                ERROR_PAGE.replace("__CODE__", str(status)).replace("__MSG__", html.escape(msg)),
            )

    def redirect(self, to):
        self.head(302, "text/html; charset=utf-8", 0, {"Location": to})

    # -- kirim byte -------------------------------------------------------
    def pump(self, f, offset, length):
        """Kirim potongan file. socket.sendfile() = zero-copy di kernel kalau bisa,
        dan dia yang ngurus socket ber-timeout (yang secara internal non-blocking)."""
        if length <= 0:
            return 0
        self.wfile.flush()
        try:
            return self.connection.sendfile(f, offset, length)
        except (AttributeError, ValueError, NotImplementedError):
            pass  # platform nggak dukung -> salin manual
        sent = 0
        f.seek(offset)
        while sent < length:
            block = f.read(min(CHUNK, length - sent))
            if not block:
                break
            self.wfile.write(block)
            sent += len(block)
        self.wfile.flush()
        return sent

    def serve_file(self, path, name, force_dl=False):
        if not os.path.isfile(path):
            return self.fail(404, "File nggak ketemu.")
        try:
            f = open(path, "rb")
        except OSError:
            return self.fail(403, "File nggak bisa dibaca.")
        with f:
            size = os.fstat(f.fileno()).st_size  # ukuran dikunci dari handle, bukan dari listing
            rng = parse_range(self.headers.get("Range"), size)
            if rng == "unsat":
                return self.send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            ext = os.path.splitext(name)[1].lower()
            disp = "attachment" if (force_dl or ext not in INLINE_EXT) else "inline"
            quoted = urllib.parse.quote(name)
            extra = {
                "Accept-Ranges": "bytes",
                "Content-Disposition": f"{disp}; filename*=UTF-8''{quoted}",
                "Last-Modified": self.date_time_string(os.path.getmtime(path)),
            }
            if isinstance(rng, tuple):
                lo, hi = rng
                extra["Content-Range"] = f"bytes {lo}-{hi}/{size}"
                status, start, length = 206, lo, hi - lo + 1
            else:
                status, start, length = 200, 0, size
            self.head(status, ctype, length, extra)
            if self.command == "HEAD":
                return
            sent = self.pump(f, start, length)
            tail = f" (lanjut dari {human(start)})" if start else ""
            if sent < length:
                fmt.log(
                    f"{C.dim('[' + self.ip + ']')} {name} {C.warn('batal')} di {human(start + sent)}"
                )
            else:
                fmt.log(
                    f"{C.dim('[' + self.ip + ']')} {name} {C.ok(str(status))} {human(length)}{tail}"
                )

    # -- ZIP --------------------------------------------------------------
    def serve_zip(self, paths):
        if len(paths) > 1:
            # pilihan: beberapa item, tiap-tiap dibungkus nama foldernya sendiri
            entries = []
            for pp in paths:
                if not pp:
                    continue
                try:
                    t = resolve(pp)
                except (Missing, Denied):
                    continue
                base = os.path.basename(t.rstrip(os.sep)) or pp.split("/")[-1] or "item"
                entries.append((base, t))
            files, total = [], 0
            for base, t in entries:
                for path, arc in walk_files(t, prefix=base):
                    try:
                        total += os.path.getsize(path)
                    except OSError:
                        continue
                    files.append((path, arc))
            if not files:
                return self.fail(404, "Nggak ada yang bisa di-download.")
            name = "pilihan.zip"
            key = "\x00".join(sorted(x for x in paths if x))
        else:
            p = paths[0] if paths else ""
            try:
                target = resolve(p)
            except (Missing, Denied):
                return self.fail(404, "Nggak ketemu.")
            files, total = [], 0
            for path, arc in zip_entries(p, target):
                try:
                    total += os.path.getsize(path)
                except OSError:
                    continue
                files.append((path, arc))
            if not files:
                return self.fail(404, "Folder ini kosong.")
            name = zip_name(p, target)
            key = p
        quoted = urllib.parse.quote(name)
        disp = f"attachment; filename*=UTF-8''{quoted}"

        if total <= ST.cfg.zip_resume_limit:
            segments, zsize = self.zip_plan(key, files, total)
            rng = parse_range(self.headers.get("Range"), zsize)
            if rng == "unsat":
                return self.send(416, b"", "text/plain", {"Content-Range": f"bytes */{zsize}"})
            extra = {"Accept-Ranges": "bytes", "Content-Disposition": disp}
            if isinstance(rng, tuple):
                lo, hi = rng
                extra["Content-Range"] = f"bytes {lo}-{hi}/{zsize}"
                status, start, end = 206, lo, hi + 1
            else:
                status, start, end = 200, 0, zsize
            self.head(status, "application/zip", end - start, extra)
            if self.command == "HEAD":
                return

            def put_file(path, off, n, size, mtime):
                # Dicek dua kali: sebelum dan sesudah dikirim. Yang kedua nangkep file
                # yang berubah pas lagi di tengah transfer.
                st = os.stat(path)
                if st.st_size != size or int(st.st_mtime) != mtime:
                    raise Stale(os.path.basename(path))
                with open(path, "rb") as fh:
                    sent = self.pump(fh, off, n)
                st = os.stat(path)
                if sent != n or st.st_size != size or int(st.st_mtime) != mtime:
                    raise Stale(os.path.basename(path))

            emit_plan(segments, start, end, self.wfile.write, put_file)
            fmt.log(
                f"{C.dim('[' + self.ip + ']')} {name} {C.ok(str(status))} {human(end - start)} (bisa resume)"
            )
            return

        # Kegedean buat pra-hitung CRC: streaming, konsekuensinya nggak bisa resume.
        self.head(200, "application/zip", None, {"Content-Disposition": disp}, close=True)
        if self.command == "HEAD":
            return
        zf = zipfile.ZipFile(StreamWriter(self.wfile), "w", zipfile.ZIP_STORED, allowZip64=True)
        for path, arc in files:
            zf.write(path, arc.replace(os.sep, "/"))
        zf.close()
        self.wfile.flush()
        fmt.log(f"{C.dim('[' + self.ip + ']')} {name} {C.ok('200')} streaming {human(total)}")

    def zip_plan(self, p, files, total):
        sig = (len(files), total, max((os.path.getmtime(f) for f, _ in files), default=0))
        with ST.lock:
            hit = ST.zip_plans.get(p)
        if hit and hit[0] == sig:
            return hit[1], hit[2]
        t0 = time.monotonic()
        if total > (1 << 30):
            fmt.log(f"Nyiapin ZIP ({human(total)}) - ngitung CRC biar bisa di-resume...")
        segments, zsize = build_zip_plan(files)
        if total > (1 << 30):
            fmt.log(f"ZIP siap dalam {time.monotonic() - t0:.1f}s")
        with ST.lock:
            ST.zip_plans[p] = (sig, segments, zsize)
        return segments, zsize

    # -- upload -----------------------------------------------------------
    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if not self.authed(qs):
            return self.fail(401, "Perlu kode akses.")
        if parsed.path != "/up":
            return self.fail(404, "Nggak ada.")
        if ST.cfg.read_only:
            return self.fail(403, "Upload dimatiin.")
        p = self.qget(qs, "p")
        try:
            target = resolve(p)
        except Missing:
            return self.fail(404, "Folder nggak ketemu.")
        except Denied:
            return self.fail(403, "Di luar folder yang dibagikan.")
        if target is None or not os.path.isdir(target):
            return self.fail(404, "Bukan folder.")
        if not os.access(target, os.W_OK):
            return self.fail(403, "Folder ini nggak bisa ditulisi.")
        folder = target
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return self.fail(411, "Content-Length wajib ada.")
        if ST.cfg.max_upload and length > ST.cfg.max_upload:
            return self.fail(413, f"Maksimal {human(ST.cfg.max_upload)} per file.")
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            free = None
        if free is not None and length + (1 << 30) > free:
            return self.fail(507, "Sisa disk nggak cukup.")

        name = safe_upload_name(self.qget(qs, "name"))
        dest = unique_path(folder, name)
        tmp = dest + ".part"
        got = 0
        try:
            with open(tmp, "wb") as out:
                while got < length:
                    block = self.rfile.read(min(CHUNK, length - got))
                    if not block:
                        break
                    out.write(block)
                    got += len(block)
            if got != length:
                raise OSError("koneksi putus")
            os.replace(tmp, dest)
        except Exception as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            fmt.log(f"{C.dim('[' + self.ip + ']')} upload {name} {C.bad('gagal')}: {e}")
            self.close_connection = True
            return
        fmt.log(
            f"{C.dim('[' + self.ip + ']')} {C.accent('UPLOAD')} {os.path.basename(dest)} {human(got)}"
        )
        self.send_json({"ok": True, "name": os.path.basename(dest)})

    # -- login ------------------------------------------------------------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/login":
            return self.fail(404, "Nggak ada.")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        body = self.rfile.read(min(n, 4096)).decode("utf-8", "replace")
        form = urllib.parse.parse_qs(body)
        code = (form.get("code") or [""])[0].strip()
        nxt = (form.get("next") or ["/"])[0]
        if not nxt.startswith("/"):
            nxt = "/"
        wait = lock_left(self.ip)
        if wait:
            return self.send(
                429, login_page(f"Kebanyakan salah. Coba lagi {wait} detik lagi.", nxt)
            )
        if check_code(self.ip, code):
            self.pending_cookie = new_session()
            return self.redirect(nxt)
        left = lock_left(self.ip)
        msg = f"Kode salah. Dikunci {left} detik." if left else "Kode salah, coba lagi."
        return self.send(401, login_page(msg, nxt))

    # -- GET / HEAD -------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if route == "/favicon.ico":
            return self.send(204, b"", "image/x-icon")
        if route == "/login":
            if valid_session(self.cookie_token()):
                return self.redirect("/")
            nxt = self.qget(qs, "next", "/")
            if not nxt.startswith("/"):
                nxt = "/"
            return self.send(200, login_page(nxt=nxt))

        if not self.authed(qs):
            if route.startswith("/api/"):
                return self.fail(401, "Perlu kode akses.")
            nxt = urllib.parse.quote(self.path)
            return self.redirect(f"/login?next={nxt}")

        if route == "/":
            # Kode di URL (dari QR) langsung ditukar cookie, terus dibuang dari URL
            # biar nggak nyangkut di history HP.
            if "code" in qs:
                keep = {k: v for k, v in qs.items() if k != "code"}
                tail = urllib.parse.urlencode(keep, doseq=True)
                return self.redirect("/?" + tail if tail else "/")
            return self.send(200, page_html())

        if route == "/api/rev":
            return self.send_json({"rev": ST.revision})

        if route == "/qr":
            u = self.qget(qs, "u")
            if not u:
                return self.fail(400, "Butuh parameter u.")
            return self.send(
                200, qr_svg(u), "image/svg+xml; charset=utf-8", {"Cache-Control": "no-store"}
            )

        p = self.qget(qs, "p")
        try:
            target = resolve(p)
        except Missing:
            return self.fail(404, "Nggak ketemu.")
        except Denied:
            return self.fail(403, "Di luar folder yang dibagikan.")

        try:
            if route == "/api/list":
                return self.api_list(p, target, qs)
            if route == "/api/zipinfo":
                if target is not None and not os.path.isdir(target):
                    return self.fail(404, "Bukan folder.")
                count, size, cut = total_stats(target)
                return self.send_json(
                    {
                        "count": count,
                        "size": size,
                        "truncated": cut,
                        "resumable": (not cut) and size <= ST.cfg.zip_resume_limit,
                    }
                )
            if route == "/api/hash":
                if target is None or not os.path.isfile(target):
                    return self.fail(404, "Bukan file.")
                return self.send_json({"sha256": sha256_of(target)})
            if route == "/dl":
                if target is None:
                    return self.fail(404, "Bukan file.")
                return self.serve_file(
                    target, os.path.basename(target), force_dl=self.qget(qs, "dl") == "1"
                )
            if route == "/zip":
                return self.serve_zip(qs.get("p") or [""])
            if route == "/urls":
                return self.serve_urls(p, target)
            if route == "/sums":
                return self.serve_sums(p, target)
            if route == "/thumb":
                if target is None:
                    return self.fail(404, "Bukan file.")
                thumb = make_thumb(target)
                if not thumb:
                    return self.fail(404, "Nggak ada thumbnail.")
                data, ctype = thumb
                return self.send(200, data, ctype, {"Cache-Control": "private, max-age=86400"})
            if route == "/preview":
                if target is None or not os.path.isfile(target):
                    return self.fail(404, "Bukan file.")
                name = os.path.basename(target)
                ext = os.path.splitext(name)[1].lower()
                # GroupDocs ngerjain format Office/CAD/ebook; kalau nggak kepasang,
                # format itu yang dideny - PDF/gambar/media/teks tetap fallback native.
                if not HAVE_GROUPDOCS and ext in DOC_EXT:
                    return self.fail(404, "Preview dokumen butuh groupdocs-viewer-net.")
                if ext in (".txt", ".md", ".csv") or previewable(name):
                    if HAVE_GROUPDOCS and ext in DOC_EXT:
                        try:
                            data, _ = render_html(target)
                        except RenderError as e:
                            return self.fail(422, f"Dokumen nggak bisa di-render: {e}")
                        return self.send(
                            200,
                            data,
                            "text/html; charset=utf-8",
                            {"Cache-Control": "private, max-age=300"},
                        )
                    return self.serve_file(target, name)  # inline (teks/media/pdf)
                if ext in INLINE_EXT:
                    return self.serve_file(target, name)
                return self.fail(404, "Format ini nggak ada preview-nya.")
        except (BrokenPipeError, ConnectionResetError):
            fmt.log(f"{C.dim('[' + self.ip + ']')} {C.warn('dibatalin klien')}")
            self.close_connection = True
            return
        except Stale as e:
            fmt.log(f"{C.dim('[' + self.ip + ']')} {C.bad('putus')}: {e} berubah pas lagi dikirim")
            self.close_connection = True
            return
        return self.fail(404, "Nggak ada.")

    # -- endpoint kecil ---------------------------------------------------
    def api_list(self, p, target, qs):
        try:
            entries = list_entries(p, target)
        except Missing:
            return self.fail(404, "Folder nggak ketemu.")
        try:
            cursor = max(0, int(self.qget(qs, "cursor", "0")))
        except ValueError:
            cursor = 0
        page = entries[cursor : cursor + 2000]
        nxt = cursor + 2000 if cursor + 2000 < len(entries) else None
        parent = None
        if p:
            parent = p.rsplit("/", 1)[0] if "/" in p else ""
            if ST.single_root and parent == "":
                parent = None
        return self.send_json(
            {
                "path": p,
                "parent": parent,
                "total": len(entries),
                "entries": page,
                "next_cursor": nxt,
                "rev": ST.revision,
                "can_upload": can_upload_here(target),
                "single_root": ST.single_root,
                "shared_count": len(ST.mounts),
            }
        )

    def base(self):
        host = self.headers.get("Host") or f"{ST.cfg.host}:{ST.cfg.port}"
        return f"http://{host}"

    def serve_urls(self, p, target):
        base = self.base()
        lines = []
        for _path, arc in zip_entries(p, target):
            rel = f"{p}/{arc}" if p else arc
            rel = rel.replace(os.sep, "/")
            lines.append(f"{base}/dl?p={urllib.parse.quote(rel)}&dl=1&code={ST.code}")
        body = "\n".join(lines) + "\n"
        return self.send(200, body.encode(), "text/plain; charset=utf-8")

    def serve_sums(self, p, target):
        out = []
        for path, arc in zip_entries(p, target):
            try:
                out.append(f"{sha256_of(path)}  {arc.replace(os.sep, '/')}")
            except OSError:
                continue
        body = "\n".join(out) + "\n"
        name = (zip_name(p, target)[:-4] or "share") + ".SHA256SUMS"
        return self.send(
            200,
            body.encode(),
            "text/plain; charset=utf-8",
            {"Content-Disposition": f'attachment; filename="{name}"'},
        )


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
