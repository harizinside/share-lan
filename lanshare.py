#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["qrcode", "pillow"]
# ///
"""
LAN Share - drop path apa aja, orang lain tinggal scan & download.

    uv run lanshare.py ~/Desktop/video.mp4 ~/Foto\\ Liburan/

Seret file/folder dari Finder ke terminal buat nempelin path-nya.
Penerima buka IP + kode 4 digit, atau tinggal scan QR.
"""

import argparse
import errno
import hashlib
import html
import io
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import qrcode

try:
    from PIL import Image, ImageOps

    HAVE_PIL = True
except Exception:  # Pillow opsional - tanpa dia thumbnail mati, sisanya jalan
    HAVE_PIL = False

APP = "lanshare"
CHUNK = 1 << 20
ZIP64_LIMIT = 0xFFFFFFFF
SESSION_TTL = 86400
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".opus"}
INLINE_EXT = IMAGE_EXT | VIDEO_EXT | AUDIO_EXT | {".pdf", ".txt", ".md"}
WIN_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class State:
    """Semua yang dibagi antar-thread, dikumpulin di satu tempat."""

    def __init__(self):
        self.cfg = None
        self.mounts = {}  # nama tampilan -> path absolut di disk
        self.code = ""
        self.sessions = {}  # token -> waktu kedaluwarsa
        self.fails = {}  # ip -> [gagal, dikunci_sampai, ronde]
        self.global_fails = 0
        self.upload_dir = None
        self.initial_path = ""
        self.single_file = None
        self.base_url = ""
        self.lock = threading.Lock()
        self.crc_cache = {}  # (path, size, mtime) -> crc32
        self.hash_cache = {}  # (path, size, mtime) -> sha256
        self.zip_plans = {}  # path -> (tanda tangan isi, segmen, ukuran)
        self.single_root = False  # cuma 1 folder yang dishare -> folder itu jadi root
        self.addresses = []  # [(ip, iface, skor)]
        self.revision = 0  # naik tiap daftar bagikan berubah; klien polling ini


ST = State()


# --------------------------------------------------------------------------
# format & warna
# --------------------------------------------------------------------------


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def parse_size(text):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?)b?\s*", str(text), re.I)
    if not m:
        raise argparse.ArgumentTypeError(f"ukuran nggak kebaca: {text!r} (contoh: 500M, 20G)")
    mult = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30, "t": 1 << 40}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


class C:
    """Warna ANSI, mati sendiri kalau output-nya di-pipe."""

    on = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    @classmethod
    def _w(cls, code, s):
        return f"\033[{code}m{s}\033[0m" if cls.on else str(s)

    @classmethod
    def dim(cls, s):
        return cls._w("2", s)

    @classmethod
    def bold(cls, s):
        return cls._w("1", s)

    @classmethod
    def accent(cls, s):
        return cls._w("38;5;141", s)

    @classmethod
    def ok(cls, s):
        return cls._w("38;5;114", s)

    @classmethod
    def warn(cls, s):
        return cls._w("38;5;214", s)

    @classmethod
    def bad(cls, s):
        return cls._w("38;5;203", s)


def log(msg):
    print(f"{C.dim(time.strftime('%H:%M:%S'))}  {msg}", flush=True)


def die(msg):
    print(f"\n  {C.bad('✗')} {msg}\n", file=sys.stderr)
    sys.exit(1)


# --------------------------------------------------------------------------
# mount: path yang lu drop -> nama yang keliatan penerima
# --------------------------------------------------------------------------


def unique_name(name, taken):
    if name not in taken:
        return name
    stem, ext = os.path.splitext(name)
    i = 2
    while f"{stem} ({i}){ext}" in taken:
        i += 1
    return f"{stem} ({i}){ext}"


def build_mounts(paths):
    mounts = {}
    bad = []
    for raw in paths:
        p = os.path.realpath(os.path.expanduser(raw))
        if not os.path.exists(p):
            bad.append((raw, "nggak ada"))
            continue
        if not os.access(p, os.R_OK):
            bad.append((raw, "nggak bisa dibaca"))
            continue
        name = os.path.basename(p.rstrip(os.sep)) or p.replace(os.sep, "_")
        mounts[unique_name(name, mounts)] = p
    if bad:
        lines = "\n".join(f"      {C.bad(p)}  ({why})" for p, why in bad)
        die(f"Path ini bermasalah, server nggak jalan:\n{lines}")
    return mounts


def recompute():
    """Dipanggil tiap daftar bagikan berubah: nentuin bentuk root + tujuan upload."""
    items = list(ST.mounts.items())
    ST.single_root = len(items) == 1 and os.path.isdir(items[0][1])
    ST.initial_path = items[0][0] if ST.single_root else ""
    ST.single_file = items[0][0] if len(items) == 1 and os.path.isfile(items[0][1]) else None
    if not ST.cfg.upload_to:
        ST.upload_dir = next(
            (p for p in ST.mounts.values() if os.path.isdir(p) and os.access(p, os.W_OK)),
            None,
        )
    ST.revision += 1


def add_paths(raws):
    """Tambahin path ke daftar bagikan sambil server jalan.

    Yang disimpan cuma path-nya. Nggak ada file yang disalin ke mana pun -
    datanya dibaca langsung dari tempat aslinya pas ada yang download.
    """
    added, skipped, bad = [], [], []
    with ST.lock:
        punya = set(ST.mounts.values())
        for raw in raws:
            path = os.path.realpath(os.path.expanduser(raw))
            if not os.path.exists(path):
                bad.append((raw, "nggak ada"))
            elif not os.access(path, os.R_OK):
                bad.append((raw, "nggak bisa dibaca"))
            elif path in punya:
                skipped.append(raw)
            else:
                name = unique_name(os.path.basename(path.rstrip(os.sep)) or path, ST.mounts)
                ST.mounts[name] = path
                punya.add(path)
                added.append((name, path))
    if added:
        recompute()
    return added, skipped, bad


def remove_mount(key):
    """Cabut satu item dari daftar bagikan - lewat namanya atau nomor urutnya."""
    with ST.lock:
        names = list(ST.mounts)
        name = None
        if key in ST.mounts:
            name = key
        elif key.isdigit() and 1 <= int(key) <= len(names):
            name = names[int(key) - 1]
        if name:
            del ST.mounts[name]
    if name:
        recompute()
    return name


class Denied(Exception):
    """Path di luar mount - 403."""


class Missing(Exception):
    """Mount atau file nggak ketemu - 404."""


def resolve(p):
    """'<mount>/<sisa/path>' -> path absolut, dijamin masih di dalam mount-nya."""
    p = (p or "").replace("\\", "/").strip("/")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts:
        return None  # root virtual = daftar mount
    base = ST.mounts.get(parts[0])
    if base is None:
        raise Missing(parts[0])
    target = os.path.realpath(os.path.join(base, *parts[1:])) if len(parts) > 1 else base
    if target != base and not target.startswith(base + os.sep):
        raise Denied(p)  # nutup ../ sekaligus symlink yang nunjuk keluar
    return target


def visible(name):
    if name.endswith(".part"):
        return False
    return ST.cfg.hidden or not name.startswith(".")


def entry_of(name, full, rel):
    try:
        st = os.stat(full)
    except OSError:
        return None
    is_dir = os.path.isdir(full)
    return {
        "name": name,
        "path": rel,
        "dir": is_dir,
        "size": None if is_dir else st.st_size,
        "mtime": int(st.st_mtime),
        "kind": kind_of(name, is_dir),
    }


def kind_of(name, is_dir):
    if is_dir:
        return "dir"
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".dmg", ".iso"}:
        return "archive"
    if ext in {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".txt",
        ".md",
        ".csv",
        ".rtf",
    }:
        return "doc"
    if ext in {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".json",
        ".html",
        ".css",
        ".go",
        ".rs",
        ".c",
        ".h",
        ".cpp",
        ".java",
        ".rb",
        ".php",
        ".sh",
        ".yml",
        ".yaml",
        ".toml",
        ".sql",
    }:
        return "code"
    return "file"


def list_entries(p, target):
    """Daftar isi buat /api/list. p kosong = daftar mount."""
    out = []
    if target is None:
        for name, full in ST.mounts.items():
            e = entry_of(name, full, name)
            if e:
                out.append(e)
    else:
        if not os.path.isdir(target):
            raise Missing(p)
        with os.scandir(target) as it:
            for de in it:
                if not visible(de.name):
                    continue
                e = entry_of(de.name, de.path, f"{p}/{de.name}" if p else de.name)
                if e:
                    out.append(e)
    out.sort(key=lambda e: (not e["dir"], e["name"].casefold()))
    return out


def walk_files(target, prefix=""):
    """Semua file di bawah target, urut stabil. Yield (abspath, arcname)."""
    if os.path.isfile(target):
        yield target, prefix or os.path.basename(target)
        return
    for root, dirs, files in os.walk(target, followlinks=False):
        dirs[:] = sorted(d for d in dirs if visible(d))
        rel = os.path.relpath(root, target)
        for name in sorted(f for f in files if visible(f)):
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            arc = name if rel == "." else os.path.join(rel, name)
            yield full, os.path.join(prefix, arc) if prefix else arc


def dir_stats(target, budget=2.0):
    """(jumlah file, total byte, kepotong?) - dibatesin waktu biar startup nggak ngegantung."""
    if os.path.isfile(target):
        try:
            return 1, os.path.getsize(target), False
        except OSError:
            return 0, 0, False
    deadline = time.monotonic() + budget
    count = size = 0
    for root, dirs, files in os.walk(target, followlinks=False):
        dirs[:] = [d for d in dirs if visible(d)]
        for name in files:
            if not visible(name):
                continue
            try:
                size += os.path.getsize(os.path.join(root, name))
                count += 1
            except OSError:
                pass
        if time.monotonic() > deadline:
            return count, size, True
    return count, size, False


def total_stats(target):
    """Statistik buat keputusan ZIP. target None = semua mount."""
    if target is not None:
        return dir_stats(target)
    count = size = 0
    cut = False
    for full in ST.mounts.values():
        c, sz, t = dir_stats(full, budget=1.0)
        count += c
        size += sz
        cut = cut or t
    return count, size, cut


def safe_upload_name(raw):
    """Nama file dari luar itu nggak dipercaya sama sekali."""
    name = urllib.parse.unquote(raw or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    name = name.strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    if not name or name in (".", ".."):
        name = "upload"
    stem, ext = os.path.splitext(name)
    if stem.lower() in WIN_RESERVED:
        stem = f"_{stem}"
    if len(stem) > 200:
        stem = stem[:200]
    if ext.lower() == ".part":
        ext = ".part.bin"
    return stem + ext[:20]


# --------------------------------------------------------------------------
# auth: kode 4 digit + rem brute-force
# --------------------------------------------------------------------------


def new_code(length):
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def new_session():
    token = secrets.token_urlsafe(32)
    with ST.lock:
        now = time.time()
        for t, exp in list(ST.sessions.items()):
            if exp < now:
                del ST.sessions[t]
        ST.sessions[token] = now + SESSION_TTL
    return token


def valid_session(token):
    if not token:
        return False
    with ST.lock:
        exp = ST.sessions.get(token)
        if exp is None:
            return False
        if exp < time.time():
            del ST.sessions[token]
            return False
    return True


def lock_left(ip):
    with ST.lock:
        rec = ST.fails.get(ip)
        if not rec:
            return 0
        return max(0, int(rec[1] - time.time()))


def check_code(ip, given):
    """True kalau kodenya bener. Salah -> kunci per-IP naik bertingkat."""
    if lock_left(ip):
        return False
    ok = bool(given) and secrets.compare_digest(str(given), ST.code)
    if ok:
        with ST.lock:
            ST.fails.pop(ip, None)
        return True

    rotated = False
    with ST.lock:
        rec = ST.fails.setdefault(ip, [0, 0.0, 0])
        rec[0] += 1
        ST.global_fails += 1
        if rec[0] >= 5:
            rec[2] += 1
            wait = min(60 * (2 ** (rec[2] - 1)), 900)
            rec[1] = time.time() + wait
            rec[0] = 0
            print(
                f"\n  {C.warn('⚠')} 5x kode salah dari {C.bold(ip)} - dikunci {wait // 60}m {wait % 60}s\n",
                flush=True,
            )
        # Di LAN, ganti IP itu gampang, jadi kunci per-IP doang bisa diakalin.
        if ST.global_fails >= 50:
            ST.global_fails = 0
            ST.code = new_code(ST.cfg.code_len)
            rotated = True
    if rotated:
        print(f"\n  {C.warn('⚠')} Kebanyakan tebakan salah. Kode diganti otomatis.\n", flush=True)
        print_banner(reprint=True)
    return False


# --------------------------------------------------------------------------
# QR
# --------------------------------------------------------------------------


def qr_matrix(data):
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(data)
    q.make(fit=True)
    return q


def qr_svg(data, box=8):
    m = qr_matrix(data).get_matrix()
    n = len(m)
    side = n * box
    rects = []
    for y, row in enumerate(m):  # gabungin kotak sebaris jadi satu rect
        x = 0
        while x < n:
            if row[x]:
                start = x
                while x < n and row[x]:
                    x += 1
                rects.append(
                    f'<rect x="{start * box}" y="{y * box}" width="{(x - start) * box}" height="{box}"/>'
                )
            else:
                x += 1
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}" height="{side}" '
        f'viewBox="0 0 {side} {side}" shape-rendering="crispEdges">'
        f'<rect width="{side}" height="{side}" fill="#fff"/>'
        f'<g fill="#000">{"".join(rects)}</g></svg>'
    ).encode()


def qr_ascii(data):
    buf = io.StringIO()
    qr_matrix(data).print_ascii(out=buf, invert=True)
    return buf.getvalue().rstrip("\n")


# --------------------------------------------------------------------------
# checksum & thumbnail
# --------------------------------------------------------------------------


def file_key(path):
    st = os.stat(path)
    return (path, st.st_size, int(st.st_mtime))


def crc32_of(path):
    key = file_key(path)
    with ST.lock:
        hit = ST.crc_cache.get(key)
    if hit is not None:
        return hit
    crc = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            crc = zlib.crc32(b, crc)
    crc &= 0xFFFFFFFF
    with ST.lock:
        ST.crc_cache[key] = crc
    return crc


def sha256_of(path):
    key = file_key(path)
    with ST.lock:
        hit = ST.hash_cache.get(key)
    if hit is not None:
        return hit
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    digest = h.hexdigest()
    with ST.lock:
        ST.hash_cache[key] = digest
    return digest


def thumb_cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    d = os.path.join(base, APP, "thumbs")
    os.makedirs(d, exist_ok=True)
    return d


def make_thumb(path, box=360):
    """JPEG kecil buat preview. None kalau nggak bisa - UI-nya mundur ke ikon."""
    if not HAVE_PIL:
        return None
    if os.path.splitext(path)[1].lower() not in IMAGE_EXT:
        return None
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha1(f"{path}|{st.st_size}|{int(st.st_mtime)}|{box}".encode()).hexdigest()
    cached = os.path.join(thumb_cache_dir(), key + ".jpg")
    try:
        with open(cached, "rb") as f:
            return f.read()
    except OSError:
        pass
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((box, box))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=82, optimize=True)
            data = buf.getvalue()
    except Exception:
        return None
    try:
        tmp = cached + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, cached)
    except OSError:
        pass
    return data


# --------------------------------------------------------------------------
# ZIP
#
# Di bawah ambang: CRC dihitung duluan, jadi seluruh layout byte bisa dipetakan
# persis -> Content-Length ketauan -> Range jalan -> ZIP-nya bisa di-resume.
# Di atas ambang: streaming biasa, cepat mulai tapi nggak bisa dilanjut.
# --------------------------------------------------------------------------


class Stale(Exception):
    """File berubah di tengah jalan - lebih baik putus daripada ngirim ZIP korup."""


def dos_time(ts):
    tm = time.localtime(ts)
    if tm.tm_year < 1980:
        tm = time.localtime(315532800)
    return (tm.tm_year, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec)


def enc_filename(zi):
    try:
        return zi.filename.encode("ascii"), zi.flag_bits
    except UnicodeEncodeError:
        return zi.filename.encode("utf-8"), zi.flag_bits | 0x800


def zip_entries(p, target):
    if target is None:
        for name, full in ST.mounts.items():
            yield from walk_files(full, prefix=name)
    else:
        yield from walk_files(target, prefix="")


def zip_name(p, target):
    if target is None:
        return "share.zip"
    return os.path.basename(target.rstrip(os.sep)) + ".zip"


def _central_record(zi):
    fname, flags = enc_filename(zi)
    file_size, comp_size, offset = zi.file_size, zi.compress_size, zi.header_offset
    big = []
    if file_size > ZIP64_LIMIT or comp_size > ZIP64_LIMIT:
        big += [file_size, comp_size]
        file_size = comp_size = 0xFFFFFFFF
    if offset > ZIP64_LIMIT:
        big.append(offset)
        offset = 0xFFFFFFFF
    extra = struct.pack("<HH" + "Q" * len(big), 1, 8 * len(big), *big) if big else b""
    dt = zi.date_time
    dosdate = (dt[0] - 1980) << 9 | dt[1] << 5 | dt[2]
    dostime = dt[3] << 11 | dt[4] << 5 | (dt[5] // 2)
    extract_version = max(zi.extract_version, 45 if big else 20)
    rec = struct.pack(
        zipfile.structCentralDir,
        zipfile.stringCentralDir,
        zi.create_version,
        zi.create_system,
        extract_version,
        zi.reserved,
        flags,
        zi.compress_type,
        dostime,
        dosdate,
        zi.CRC,
        comp_size,
        file_size,
        len(fname),
        len(extra),
        0,
        0,
        zi.internal_attr,
        zi.external_attr,
        offset,
    )
    return rec + fname + extra


def _end_record(count, cd_size, cd_offset):
    out = b""
    entries, size, offset = count, cd_size, cd_offset
    if count > 0xFFFF or cd_size > ZIP64_LIMIT or cd_offset > ZIP64_LIMIT:
        out += struct.pack(
            zipfile.structEndArchive64,
            zipfile.stringEndArchive64,
            44,
            45,
            45,
            0,
            0,
            count,
            count,
            cd_size,
            cd_offset,
        )
        out += struct.pack(
            zipfile.structEndArchive64Locator,
            zipfile.stringEndArchive64Locator,
            0,
            cd_offset + cd_size,
            1,
        )
        entries = min(count, 0xFFFF)
        size = min(cd_size, ZIP64_LIMIT)
        offset = min(cd_offset, ZIP64_LIMIT)
    out += struct.pack(
        zipfile.structEndArchive,
        zipfile.stringEndArchive,
        0,
        0,
        entries,
        entries,
        size,
        offset,
        0,
    )
    return out


def build_zip_plan(entries, on_progress=None):
    """[(kind, ...)] + total byte. kind 'b' = bytes literal, 'f' = potongan file."""
    segments = []
    central = []
    offset = 0
    done = 0
    for path, arc in entries:
        try:
            st = os.stat(path)
        except OSError:
            continue
        zi = zipfile.ZipInfo(arc.replace(os.sep, "/"), dos_time(st.st_mtime))
        zi.compress_type = zipfile.ZIP_STORED
        zi.file_size = zi.compress_size = st.st_size
        zi.CRC = crc32_of(path)
        zi.external_attr = (st.st_mode & 0xFFFF) << 16
        zi.header_offset = offset
        header = zi.FileHeader(zi.file_size > ZIP64_LIMIT or offset > ZIP64_LIMIT)
        segments.append(("b", header))
        offset += len(header)
        segments.append(("f", path, st.st_size, int(st.st_mtime)))
        offset += st.st_size
        central.append(zi)
        done += st.st_size
        if on_progress:
            on_progress(done)
    cd_offset = offset
    cd = b"".join(_central_record(zi) for zi in central)
    tail = cd + _end_record(len(central), len(cd), cd_offset)
    segments.append(("b", tail))
    return segments, offset + len(tail)


def emit_plan(segments, start, end, put_bytes, put_file):
    """Kirim byte [start, end) dari rencana ZIP."""
    pos = 0
    for seg in segments:
        length = len(seg[1]) if seg[0] == "b" else seg[2]
        seg_start, seg_end = pos, pos + length
        pos = seg_end
        if seg_end <= start:
            continue
        if seg_start >= end:
            break
        lo = max(start, seg_start) - seg_start
        hi = min(end, seg_end) - seg_start
        if seg[0] == "b":
            put_bytes(seg[1][lo:hi])
        else:
            put_file(seg[1], lo, hi - lo, seg[2], seg[3])


class StreamWriter:
    """Objek tulis tanpa seek/tell - zipfile otomatis pakai mode data descriptor."""

    def __init__(self, wfile):
        self.wfile = wfile

    def write(self, data):
        self.wfile.write(data)
        return len(data)

    def flush(self):
        self.wfile.flush()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


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
                log(
                    f"{C.dim('[' + self.ip + ']')} {name} {C.warn('batal')} di {human(start + sent)}"
                )
            else:
                log(
                    f"{C.dim('[' + self.ip + ']')} {name} {C.ok(str(status))} {human(length)}{tail}"
                )

    # -- ZIP --------------------------------------------------------------
    def serve_zip(self, p, target):
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
        quoted = urllib.parse.quote(name)
        disp = f"attachment; filename*=UTF-8''{quoted}"

        if total <= ST.cfg.zip_resume_limit:
            segments, zsize = self.zip_plan(p, files, total)
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
            log(
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
        log(f"{C.dim('[' + self.ip + ']')} {name} {C.ok('200')} streaming {human(total)}")

    def zip_plan(self, p, files, total):
        sig = (len(files), total, max((os.path.getmtime(f) for f, _ in files), default=0))
        with ST.lock:
            hit = ST.zip_plans.get(p)
        if hit and hit[0] == sig:
            return hit[1], hit[2]
        t0 = time.monotonic()
        if total > (1 << 30):
            log(f"Nyiapin ZIP ({human(total)}) - ngitung CRC biar bisa di-resume...")
        segments, zsize = build_zip_plan(files)
        if total > (1 << 30):
            log(f"ZIP siap dalam {time.monotonic() - t0:.1f}s")
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
        if ST.cfg.read_only or not ST.upload_dir:
            return self.fail(403, "Upload dimatiin.")
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return self.fail(411, "Content-Length wajib ada.")
        if ST.cfg.max_upload and length > ST.cfg.max_upload:
            return self.fail(413, f"Maksimal {human(ST.cfg.max_upload)} per file.")
        try:
            free = shutil.disk_usage(ST.upload_dir).free
        except OSError:
            free = None
        if free is not None and length + (1 << 30) > free:
            return self.fail(507, "Sisa disk nggak cukup.")

        name = safe_upload_name(self.qget(qs, "name"))
        dest = unique_path(ST.upload_dir, name)
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
            log(f"{C.dim('[' + self.ip + ']')} upload {name} {C.bad('gagal')}: {e}")
            self.close_connection = True
            return
        log(
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
                return self.serve_zip(p, target)
            if route == "/urls":
                return self.serve_urls(p, target)
            if route == "/sums":
                return self.serve_sums(p, target)
            if route == "/thumb":
                if target is None:
                    return self.fail(404, "Bukan file.")
                data = make_thumb(target)
                if not data:
                    return self.fail(404, "Nggak ada thumbnail.")
                return self.send(
                    200, data, "image/jpeg", {"Cache-Control": "private, max-age=86400"}
                )
        except (BrokenPipeError, ConnectionResetError):
            log(f"{C.dim('[' + self.ip + ']')} {C.warn('dibatalin klien')}")
            self.close_connection = True
            return
        except Stale as e:
            log(f"{C.dim('[' + self.ip + ']')} {C.bad('putus')}: {e} berubah pas lagi dikirim")
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
                "can_upload": bool(ST.upload_dir) and not ST.cfg.read_only,
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


# --------------------------------------------------------------------------
# Halaman
# --------------------------------------------------------------------------

CSS = r"""
*{box-sizing:border-box}
:root{
  --bg:#f4f6fb; --blob1:#c9c2ff; --blob2:#9ad9ff;
  --card:rgba(255,255,255,.72); --card2:rgba(255,255,255,.55);
  --line:rgba(16,20,45,.10); --line2:rgba(16,20,45,.06);
  --text:#12142099; --ink:#121420; --muted:#5b6076;
  --accent:#5a48f5; --accent2:#9b4bff; --ok:#16a34a; --warn:#c2740a;
  --shadow:0 1px 2px rgba(16,20,45,.06),0 8px 24px rgba(16,20,45,.08);
  --r:14px;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme=light]){
    --bg:#0a0c12; --blob1:#3a2f7a; --blob2:#123a52;
    --card:rgba(255,255,255,.045); --card2:rgba(255,255,255,.03);
    --line:rgba(255,255,255,.10); --line2:rgba(255,255,255,.06);
    --ink:#e9ebf5; --muted:#8f95ad;
    --accent:#8b7bff; --accent2:#c07bff; --ok:#4ade80; --warn:#fbbf24;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px rgba(0,0,0,.35);
  }
}
:root[data-theme=dark]{
  --bg:#0a0c12; --blob1:#3a2f7a; --blob2:#123a52;
  --card:rgba(255,255,255,.045); --card2:rgba(255,255,255,.03);
  --line:rgba(255,255,255,.10); --line2:rgba(255,255,255,.06);
  --ink:#e9ebf5; --muted:#8f95ad;
  --accent:#8b7bff; --accent2:#c07bff; --ok:#4ade80; --warn:#fbbf24;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px rgba(0,0,0,.35);
}
html,body{margin:0;height:100%}
body{
  background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,BlinkMacSystemFont,
  "Segoe UI",Inter,Roboto,sans-serif; -webkit-font-smoothing:antialiased;
  padding-bottom:96px;
}
.bgfx{position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:
    radial-gradient(60vw 50vh at 8% -8%, var(--blob1) 0%, transparent 62%),
    radial-gradient(52vw 44vh at 96% 6%, var(--blob2) 0%, transparent 60%);
  opacity:.5; filter:blur(8px)}
button,input{font:inherit;color:inherit}
button{cursor:pointer;border:0;background:none}
a{color:inherit;text-decoration:none}
.mono{font-variant-numeric:tabular-nums}

/* header */
.top{position:sticky;top:0;z-index:30;display:flex;align-items:center;gap:10px;
  padding:12px 18px;backdrop-filter:blur(14px);
  background:linear-gradient(var(--bg),color-mix(in srgb,var(--bg) 70%,transparent));
  border-bottom:1px solid var(--line2)}
.brand{display:flex;align-items:center;gap:9px;font-weight:650;letter-spacing:-.02em}
.brand svg{width:26px;height:26px}
.brand b{color:var(--accent)}
.spacer{flex:1}
.chip{display:inline-flex;align-items:center;gap:8px;height:36px;padding:0 12px;
  border-radius:999px;border:1px solid var(--line);background:var(--card);
  font-weight:600;letter-spacing:.14em;box-shadow:var(--shadow)}
.chip small{letter-spacing:0;font-weight:500;color:var(--muted)}
.iconbtn{display:grid;place-items:center;width:36px;height:36px;border-radius:11px;
  border:1px solid var(--line);background:var(--card);box-shadow:var(--shadow);
  transition:transform .15s,border-color .15s}
.iconbtn:hover{transform:translateY(-1px);border-color:var(--accent)}
.iconbtn:active{transform:translateY(0) scale(.96)}
.iconbtn svg{width:17px;height:17px;stroke:currentColor;fill:none;stroke-width:1.7;
  stroke-linecap:round;stroke-linejoin:round}

main{max-width:1080px;margin:0 auto;padding:20px 18px 0}

/* toolbar */
.bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.crumbs{display:flex;align-items:center;gap:4px;flex:1;min-width:0;flex-wrap:wrap}
.crumbs button{padding:5px 9px;border-radius:9px;color:var(--muted);font-weight:550}
.crumbs button:hover{background:var(--card);color:var(--ink)}
.crumbs .cur{color:var(--ink);font-weight:650;font-size:16px;letter-spacing:-.01em}
.crumbs .sep{color:var(--muted);opacity:.5}
.search{display:flex;align-items:center;gap:7px;height:36px;padding:0 12px;
  border-radius:11px;border:1px solid var(--line);background:var(--card);min-width:180px}
.search svg{width:15px;height:15px;stroke:var(--muted);fill:none;stroke-width:1.8}
.search input{border:0;background:none;outline:none;width:100%}
.tools{display:flex;gap:8px}

/* aksi folder */
.actions{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.btn{display:inline-flex;align-items:center;gap:7px;height:36px;padding:0 14px;
  border-radius:11px;border:1px solid var(--line);background:var(--card);
  font-weight:600;box-shadow:var(--shadow);transition:transform .15s,border-color .15s}
.btn:hover{transform:translateY(-1px);border-color:var(--accent)}
.btn:active{transform:translateY(0) scale(.98)}
.btn svg{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:1.8;
  stroke-linecap:round;stroke-linejoin:round}
.btn.primary{background:linear-gradient(135deg,var(--accent),var(--accent2));
  color:#fff;border-color:transparent}
.note{display:inline-flex;align-items:center;gap:6px;padding:0 10px;height:36px;
  border-radius:11px;color:var(--warn);font-size:12.5px;font-weight:550;
  background:color-mix(in srgb,var(--warn) 12%,transparent)}

/* daftar */
.list{display:flex;flex-direction:column;gap:6px}
.row{display:flex;align-items:center;gap:12px;padding:9px 12px;border-radius:var(--r);
  border:1px solid transparent;transition:background .15s,border-color .15s,transform .15s;
  animation:pop .32s backwards}
.row:hover{background:var(--card);border-color:var(--line2)}
.row .thumb{width:42px;height:42px;border-radius:10px;flex:none;overflow:hidden;
  display:grid;place-items:center;background:var(--card2);border:1px solid var(--line2);
  position:relative}
.tb{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
  opacity:0;transition:opacity .25s}
.tb.on{opacity:1}
.row .name{flex:1;min-width:0;font-weight:550;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.row .meta{color:var(--muted);font-size:12.5px;flex:none}
.row .act{display:flex;gap:4px;opacity:0;transition:opacity .15s}
.row:hover .act,.row:focus-within .act{opacity:1}
@media (hover:none){.row .act{opacity:1}}
.ic{width:22px;height:22px;stroke-width:1.6;fill:none;stroke-linecap:round;stroke-linejoin:round}

/* grid */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:12px}
.card{border-radius:var(--r);overflow:hidden;border:1px solid var(--line2);
  background:var(--card);box-shadow:var(--shadow);animation:pop .32s backwards;
  transition:transform .18s}
.card:hover{transform:translateY(-3px)}
.card .ph{aspect-ratio:4/3;display:grid;place-items:center;background:var(--card2);
  overflow:hidden;position:relative}
.card .cap{padding:8px 10px;display:flex;align-items:center;gap:6px}
.card .cap span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;font-size:12.5px;font-weight:550}
.card .qr{position:absolute;top:6px;right:6px;width:28px;height:28px;border-radius:8px;
  background:rgba(0,0,0,.45);color:#fff;display:grid;place-items:center;opacity:0;
  transition:opacity .15s;backdrop-filter:blur(4px)}
.card:hover .qr{opacity:1}
@media (hover:none){.card .qr{opacity:1}}

/* kosong & skeleton */
.empty{text-align:center;padding:64px 20px;color:var(--muted)}
.empty svg{width:44px;height:44px;stroke:currentColor;fill:none;stroke-width:1.2;
  opacity:.5;margin-bottom:10px}
.sk{height:60px;border-radius:var(--r);background:linear-gradient(90deg,
  var(--card2) 25%,var(--card) 37%,var(--card2) 63%);
  background-size:400% 100%;animation:sh 1.3s infinite;margin-bottom:6px}

/* upload */
.ub{position:fixed;left:0;right:0;bottom:0;z-index:25;padding:12px 18px;
  border-top:1px solid var(--line2);backdrop-filter:blur(14px);
  background:color-mix(in srgb,var(--bg) 82%,transparent)}
.ub .inner{max-width:1080px;margin:0 auto;display:flex;align-items:center;gap:12px;
  flex-wrap:wrap}
.ub .hint{color:var(--muted);font-size:13px;flex:1;min-width:140px}
.jobs{max-width:1080px;margin:0 auto 8px;display:flex;flex-direction:column;gap:6px}
.job{display:flex;align-items:center;gap:10px;font-size:12.5px}
.job .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.job .pb{width:130px;height:6px;border-radius:99px;background:var(--line);overflow:hidden}
.job .pb i{display:block;height:100%;border-radius:99px;
  background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .2s}
.job .st{color:var(--muted);flex:none;min-width:112px;text-align:right}

/* drag overlay */
.drop{position:fixed;inset:0;z-index:60;display:none;place-items:center;
  background:color-mix(in srgb,var(--bg) 70%,transparent);backdrop-filter:blur(6px)}
.drop.on{display:grid}
.drop div{padding:52px 64px;border-radius:22px;border:2px dashed var(--accent);
  font-size:17px;font-weight:650;background:var(--card);animation:pulse 1.6s infinite}

/* modal QR */
.modal{position:fixed;inset:0;z-index:70;display:none;place-items:center;padding:20px;
  background:rgba(4,6,14,.62);backdrop-filter:blur(10px)}
.modal.on{display:grid;animation:fade .18s}
.sheet{background:#fff;color:#121420;border-radius:22px;padding:22px;max-width:390px;
  width:100%;text-align:center;box-shadow:0 24px 70px rgba(0,0,0,.5);animation:pop .24s}
.sheet h3{margin:0 0 4px;font-size:15px;color:#101220;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.sheet p{margin:0 0 14px;font-size:12.5px;color:#6b7080}
.sheet .qrbox{background:#fff;border-radius:14px;padding:8px;border:1px solid #eceef5}
.sheet .qrbox svg,.sheet .qrbox img{width:100%;height:auto;display:block}
.sheet .url{margin-top:12px;font-size:11.5px;color:#6b7080;word-break:break-all;
  background:#f5f6fa;border-radius:10px;padding:9px}
.sheet .row2{display:flex;gap:8px;margin-top:12px}
.sheet .row2 button{flex:1;height:38px;border-radius:11px;font-weight:600;
  border:1px solid #e6e8f0;background:#fff;color:#101220}
.sheet .row2 button.primary{background:linear-gradient(135deg,var(--accent),var(--accent2));
  color:#fff;border-color:transparent}

/* toast */
.toasts{position:fixed;left:50%;transform:translateX(-50%);bottom:104px;z-index:80;
  display:flex;flex-direction:column;gap:8px;align-items:center}
.toast{padding:9px 15px;border-radius:11px;background:var(--card);
  border:1px solid var(--line);box-shadow:var(--shadow);backdrop-filter:blur(12px);
  font-size:13px;font-weight:550;animation:pop .2s}
.toast.bad{color:var(--warn)}

@keyframes pop{from{opacity:0;transform:translateY(6px) scale(.99)}}
@keyframes fade{from{opacity:0}}
@keyframes sh{from{background-position:100% 0}to{background-position:0 0}}
@keyframes pulse{50%{transform:scale(1.015)}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
@media (max-width:560px){
  main{padding:16px 12px 0}
  .top{padding:10px 12px}
  .bar{flex-direction:column;align-items:stretch;gap:10px}
  .crumbs{width:100%}
  .tools{width:100%}
  .search{flex:1}
  .row .meta{display:none}
  .chip{letter-spacing:.1em;padding:0 10px}
  .job .pb{width:74px}
}
"""

JS = r"""
const CFG = __CFG__;
const $ = s => document.querySelector(s);
const enc = encodeURIComponent;
const deep = new URLSearchParams(location.search).get("p");
let S = {path: deep !== null ? deep : CFG.initial, entries: [], total: 0,
         filter: "", view: "auto", loading: false};

/* ---------- ikon ---------- */
const P = {
  dir:  '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  image:'<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 17 5-5 4 4 3-2 4 4"/>',
  video:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m10 9 5 3-5 3z"/>',
  audio:'<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>',
  archive:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M12 4v5m-1.5 3h3m-3 3h3"/>',
  doc:  '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  code: '<path d="m9 8-5 4 5 4M15 8l5 4-5 4"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
};
const TINT = {dir:"var(--accent)", image:"#e8735a", video:"#c07bff", audio:"#3aa6a0",
              archive:"#d99a2b", doc:"#4a8ef0", code:"#5bbf6a", file:"var(--muted)"};
const ico = (k, cls) => `<svg class="${cls||'ic'}" viewBox="0 0 24 24" stroke="${TINT[k]||TINT.file}">${P[k]||P.file}</svg>`;
const UI = {
  qr:'<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM19 19h2v2h-2M14 21h1"/></svg>',
  dl:'<svg viewBox="0 0 24 24"><path d="M12 3v12m0 0 4-4m-4 4-4-4M4 19h16"/></svg>',
  zip:'<svg viewBox="0 0 24 24"><path d="M20 7 12 3 4 7v10l8 4 8-4z"/><path d="M12 12v9M4 7l8 5 8-5"/></svg>',
  link:'<svg viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/></svg>',
  sun:'<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></svg>',
  moon:'<svg viewBox="0 0 24 24"><path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/></svg>',
  grid:'<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
  list:'<svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>',
  up:'<svg viewBox="0 0 24 24"><path d="M12 20V8m0 0-4 4m4-4 4 4M4 4h16"/></svg>',
  hash:'<svg viewBox="0 0 24 24"><path d="M10 3 8 21M16 3l-2 18M3.5 8.5h17M3 15.5h17"/></svg>',
  box:'<svg viewBox="0 0 24 24"><path d="M20 7 12 3 4 7v10l8 4 8-4z"/><path d="M4 7l8 5 8-5M12 12v9"/></svg>',
};

/* ---------- util ---------- */
function human(n){ if(n===null||n===undefined) return "";
  const u=["B","KB","MB","GB","TB"]; let i=0; n=Number(n);
  while(n>=1024&&i<u.length-1){n/=1024;i++}
  return (i?n.toFixed(1):n.toFixed(0))+" "+u[i]; }
function when(ts){ const d=new Date(ts*1000), n=new Date();
  const same=d.toDateString()===n.toDateString();
  return same ? d.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})
              : d.toLocaleDateString([], {day:"numeric",month:"short",
                  year: d.getFullYear()===n.getFullYear()?undefined:"numeric"}); }
function toast(msg, bad){ const t=document.createElement("div");
  t.className="toast"+(bad?" bad":""); t.textContent=msg; $("#toasts").append(t);
  setTimeout(()=>{t.style.opacity=0; setTimeout(()=>t.remove(),300)}, 2400); }
async function copy(text, msg){ try{ await navigator.clipboard.writeText(text); toast(msg||"Disalin"); }
  catch(e){ const a=document.createElement("textarea"); a.value=text; document.body.append(a);
    a.select(); document.execCommand("copy"); a.remove(); toast(msg||"Disalin"); } }
const dlUrl = p => `/dl?p=${enc(p)}&dl=1`;
const abs = u => location.origin + u + (u.includes("?")?"&":"?") + "code=" + CFG.code;

/* ---------- tema & tampilan ---------- */
function applyTheme(){ const t=localStorage.getItem("ls-theme");
  if(t) document.documentElement.dataset.theme=t; else delete document.documentElement.dataset.theme;
  const dark = t ? t==="dark" : matchMedia("(prefers-color-scheme:dark)").matches;
  $("#themeBtn").innerHTML = dark ? UI.sun : UI.moon; }
function toggleTheme(){ const cur=document.documentElement.dataset.theme
    || (matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");
  localStorage.setItem("ls-theme", cur==="dark"?"light":"dark"); applyTheme(); }
function viewMode(){ const v=localStorage.getItem("ls-view");
  if(v==="grid"||v==="list") return v;
  const vis=filtered(); const media=vis.filter(e=>e.kind==="image"||e.kind==="video").length;
  return (vis.length && media/vis.length>=0.5) ? "grid" : "list"; }

/* ---------- data ---------- */
async function load(path){
  S.path=path; S.loading=true; render();
  const r=await fetch(`/api/list?p=${enc(path)}`, {headers:{"Accept":"application/json"}});
  if(!r.ok){ S.loading=false;
    if(path){ toast("Folder itu udah nggak dibagikan", true); return load(""); }
    toast("Gagal memuat", true); render(); return; }
  const d=await r.json();
  S.entries=d.entries; S.total=d.total; S.parent=d.parent; S.next=d.next_cursor;
  S.rev=d.rev; S.shared=d.shared_count; S.loading=false;
  CFG.canUpload=d.can_upload; CFG.singleRoot=d.single_root;
  syncUpload();
  history.replaceState(null,"", path?`/?p=${enc(path)}`:"/");
  render(); zipInfo(path);
}
async function zipInfo(path){
  $("#zipnote").innerHTML="";
  if(!S.entries.some(e=>e.dir) && S.entries.length===0) return;
  try{
    const r=await fetch(`/api/zipinfo?p=${enc(path)}`); if(!r.ok) return;
    const d=await r.json(); if(path!==S.path) return;
    if(!d.resumable) $("#zipnote").innerHTML =
      `<span class="note">⚠ ${human(d.size)}${d.truncated?"+":""} — ZIP nggak bisa dilanjut kalau putus, mending per file</span>`;
  }catch(e){}
}
async function loadMore(){
  if(S.next===null||S.next===undefined) return;
  const btn=$("#more"); if(btn) btn.textContent="Memuat…";
  const r=await fetch(`/api/list?p=${enc(S.path)}&cursor=${S.next}`);
  if(!r.ok){ toast("Gagal memuat sisanya", true); return; }
  const d=await r.json();
  S.entries=S.entries.concat(d.entries); S.next=d.next_cursor; render();
}
const filtered = () => { const q=S.filter.trim().toLowerCase();
  return q ? S.entries.filter(e=>e.name.toLowerCase().includes(q)) : S.entries; };

/* ---------- render ---------- */
function render(){
  const parts = S.path ? S.path.split("/") : [];
  let cr = "";
  if(!CFG.singleRoot) cr += `<button data-go="">Dibagikan</button>`;
  parts.forEach((seg,i)=>{
    const target = parts.slice(0,i+1).join("/");
    const last = i===parts.length-1;
    if(i||!CFG.singleRoot) cr += `<span class="sep">/</span>`;
    cr += last ? `<span class="cur">${esc(seg)}</span>`
               : `<button data-go="${escA(target)}">${esc(seg)}</button>`;
  });
  if(!parts.length) cr = `<span class="cur">Dibagikan</span>`;
  $("#crumbs").innerHTML = cr;

  const acts=[];
  if(S.parent!==null && S.parent!==undefined)
    acts.push(`<button class="btn" data-go="${escA(S.parent)}">${UI.up}Naik</button>`);
  if(S.entries.length){
    acts.push(`<button class="btn primary" data-zip="1">${UI.zip}Download semua (ZIP)</button>`);
    acts.push(`<button class="btn" data-urls="1">${UI.link}Salin daftar URL</button>`);
    acts.push(`<button class="btn" data-qrfolder="1">${UI.qr}QR folder</button>`);
  }
  $("#folderActions").innerHTML = acts.join("") + `<span id="zipnote"></span>`;

  const box=$("#content");
  if(S.loading){ box.innerHTML=`<div class="sk"></div><div class="sk"></div><div class="sk"></div>`; return; }
  const list=filtered();
  if(!list.length){
    const kosong = !S.path && !S.shared;
    box.innerHTML = `<div class="empty">${UI.box}<div>${
      S.filter ? "Nggak ada yang cocok"
      : kosong ? "Belum ada yang dibagikan"
      : "Folder ini kosong"}</div>${
      kosong ? `<div style="margin-top:6px;font-size:12.5px;opacity:.8">
        Yang bagiin lagi nyiapin filenya — halaman ini update sendiri.</div>` : ""}</div>`;
    return; }

  const mode=viewMode();
  $("#viewBtn").innerHTML = mode==="grid" ? UI.list : UI.grid;
  box.className = mode;
  box.innerHTML = list.map((e,i)=>{
    const d=Math.min(i,22)*18, tb = thumbTag(e);
    if(mode==="grid") return `<div class="card" style="animation-delay:${d}ms">
        <div class="ph" data-open="${escA(e.path)}" data-dir="${e.dir?1:0}">${ico(e.kind,"ic")}${tb}
          <button class="qr" data-qr="${escA(e.path)}" data-isdir="${e.dir?1:0}" title="QR">${UI.qr}</button>
        </div>
        <div class="cap">${ico(e.kind,"ic")}<span title="${escA(e.name)}">${esc(e.name)}</span></div>
      </div>`;
    return `<div class="row" style="animation-delay:${d}ms">
        <div class="thumb">${ico(e.kind,"ic")}${tb}</div>
        <div class="name" data-open="${escA(e.path)}" data-dir="${e.dir?1:0}"
             title="${escA(e.name)}">${esc(e.name)}</div>
        <div class="meta mono">${e.dir?"folder":human(e.size)}</div>
        <div class="meta mono">${when(e.mtime)}</div>
        <div class="act">
          <button class="iconbtn" data-qr="${escA(e.path)}" data-isdir="${e.dir?1:0}" title="QR">${UI.qr}</button>
          ${e.dir?"":`<button class="iconbtn" data-hash="${escA(e.path)}" title="Salin SHA-256">${UI.hash}</button>`}
          <a class="iconbtn" href="${e.dir?`/zip?p=${enc(e.path)}`:dlUrl(e.path)}" title="Download">${UI.dl}</a>
        </div>
      </div>`;
  }).join("");
  if(S.total>S.entries.length) box.insertAdjacentHTML("beforeend",
    `<div class="empty" style="padding:18px 0">
       <button class="btn" id="more">Muat ${Math.min(2000,S.total-S.entries.length)} item lagi</button>
       <div style="margin-top:8px;font-size:12.5px">Nampilin ${S.entries.length} dari ${S.total}</div>
     </div>`);
  const more=$("#more"); if(more) more.onclick=loadMore;
}
const esc = s => String(s).replace(/[&<>]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const escA = s => esc(s).replace(/"/g,"&quot;");

/* Lazy-load bawaan browser: kalau thumbnail gagal, <img> dibuang dan ikonnya
   yang keliatan lagi - jadi nggak ada state kosong. */
const thumbTag = e => (CFG.thumbs && e.kind === "image")
  ? `<img class="tb" loading="lazy" decoding="async" alt="" src="/thumb?p=${enc(e.path)}"
       onload="this.classList.add('on')" onerror="this.remove()">` : "";

function syncUpload(){
  const ub=$("#ub"); if(!ub) return;
  ub.hidden = !CFG.canUpload;
  document.body.style.paddingBottom = CFG.canUpload ? "96px" : "24px";
}

/* ---------- polling: daftar bagikan bisa berubah kapan aja dari terminal ---------- */
async function checkRev(){
  if(S.loading) return;
  try{
    const r=await fetch("/api/rev"); if(!r.ok) return;
    const d=await r.json();
    if(S.rev!==undefined && d.rev!==S.rev) load(S.path);
  }catch(e){}
}
setInterval(()=>{ if(!document.hidden) checkRev(); }, 3000);
// HP yang baru dibuka lagi jangan nunggu tick berikutnya
addEventListener("visibilitychange", ()=>{ if(!document.hidden) checkRev(); });

/* ---------- QR ---------- */
function showQR(url, title, sub){
  $("#sheetTitle").textContent=title;
  $("#sheetSub").textContent=sub||"";
  $("#sheetUrl").textContent=url;
  $("#qrbox").innerHTML=`<img alt="QR" src="/qr?u=${enc(url)}">`;
  $("#modal").classList.add("on");
  $("#sheetCopy").onclick=()=>copy(url,"Link disalin");
  $("#sheetOpen").onclick=()=>{ location.href=url; };
}

/* ---------- upload ---------- */
function upload(files){
  [...files].forEach(f=>{
    const el=document.createElement("div"); el.className="job";
    el.innerHTML=`<span class="nm">${esc(f.name)}</span>
      <span class="pb"><i style="width:0%"></i></span><span class="st">0%</span>`;
    $("#jobs").append(el);
    const bar=el.querySelector("i"), st=el.querySelector(".st");
    const xhr=new XMLHttpRequest(); const t0=Date.now();
    xhr.open("PUT", `/up?name=${enc(f.name)}`);
    xhr.upload.onprogress=ev=>{
      if(!ev.lengthComputable) return;
      const pct=ev.loaded/ev.total, sec=(Date.now()-t0)/1000;
      const sp=ev.loaded/Math.max(sec,.001), left=(ev.total-ev.loaded)/Math.max(sp,1);
      bar.style.width=(pct*100).toFixed(1)+"%";
      st.textContent=`${human(sp)}/s · ${left>90?Math.round(left/60)+"m":Math.round(left)+"s"}`;
    };
    xhr.onload=()=>{ if(xhr.status<300){ bar.style.width="100%"; st.textContent="selesai ✓";
        setTimeout(()=>el.remove(),1800); load(S.path); }
      else { st.textContent="gagal"; el.style.color="var(--warn)";
        try{toast(JSON.parse(xhr.responseText).error,true)}catch(e){toast("Upload gagal",true)} } };
    xhr.onerror=()=>{ st.textContent="gagal"; toast("Upload gagal",true); };
    xhr.send(f);
  });
}

/* ---------- event ---------- */
document.addEventListener("click", ev=>{
  const t=ev.target.closest("[data-go],[data-open],[data-qr],[data-hash],[data-zip],[data-urls],[data-qrfolder]");
  if(!t) return;
  if(t.dataset.go!==undefined){ load(t.dataset.go); return; }
  if(t.dataset.open!==undefined){
    if(t.dataset.dir==="1") load(t.dataset.open);
    else location.href=`/dl?p=${enc(t.dataset.open)}`;
    return; }
  if(t.dataset.qr!==undefined){ ev.preventDefault();
    const p=t.dataset.qr, dir=t.dataset.isdir==="1";
    const u=abs(dir?`/zip?p=${enc(p)}`:dlUrl(p));
    showQR(u, p.split("/").pop(), dir?"Scan buat download folder (ZIP)":"Scan buat download file ini");
    return; }
  if(t.dataset.qrfolder!==undefined){
    showQR(abs(S.path?`/?p=${enc(S.path)}`:"/"), S.path.split("/").pop()||"Semua file",
           "Scan buat buka daftar ini"); return; }
  if(t.dataset.zip!==undefined){ location.href=`/zip?p=${enc(S.path)}`; return; }
  if(t.dataset.urls!==undefined){
    fetch(`/urls?p=${enc(S.path)}`).then(r=>r.text()).then(txt=>{
      const n=txt.trim().split("\n").length;
      copy(txt, `${n} URL disalin — pakai: aria2c -i list.txt`); }); return; }
  if(t.dataset.hash!==undefined){ ev.preventDefault(); toast("Ngitung SHA-256...");
    fetch(`/api/hash?p=${enc(t.dataset.hash)}`).then(r=>r.json())
      .then(d=>d.sha256?copy(d.sha256,"SHA-256 disalin"):toast("Gagal",true)); return; }
});
$("#modal").onclick = e => { if(e.target.id==="modal") $("#modal").classList.remove("on"); };
addEventListener("keydown", e=>{ if(e.key==="Escape") $("#modal").classList.remove("on");
  if(e.key==="/" && document.activeElement!==$("#q")){ e.preventDefault(); $("#q").focus(); } });
$("#q").oninput = e => { S.filter=e.target.value; render(); };
$("#themeBtn").onclick = toggleTheme;
$("#viewBtn").onclick = () => { localStorage.setItem("ls-view", viewMode()==="grid"?"list":"grid"); render(); };
$("#codeChip").onclick = () => showQR(abs("/"), "Kode akses "+CFG.code, "Scan buat masuk tanpa ngetik");
$("#qrTop").onclick = () => showQR(abs(S.path?`/?p=${enc(S.path)}`:"/"), "Halaman ini", "Scan buat buka di HP");

$("#pick").onchange = e => { upload(e.target.files); e.target.value=""; };
let depth=0;
addEventListener("dragenter", e=>{ if(!CFG.canUpload) return; e.preventDefault();
  if(++depth===1) $("#drop").classList.add("on"); });
addEventListener("dragover", e=>{ if(CFG.canUpload) e.preventDefault(); });
addEventListener("dragleave", e=>{ if(!CFG.canUpload) return;
  if(--depth<=0){ depth=0; $("#drop").classList.remove("on"); } });
addEventListener("drop", e=>{ if(!CFG.canUpload) return; e.preventDefault(); depth=0;
  $("#drop").classList.remove("on");
  if(e.dataTransfer.files.length) upload(e.dataTransfer.files); });
$("#qrTop").innerHTML = UI.qr;
syncUpload();
applyTheme();
load(S.path);
"""

SHELL = r"""<!doctype html>
<html lang="id"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>__TITLE__</title>
<style>__CSS__</style>
</head><body>
<div class="bgfx"></div>
<header class="top">
  <div class="brand">
    <svg viewBox="0 0 24 24" fill="none" stroke="url(#g)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
      <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="var(--accent)"/><stop offset="1" stop-color="var(--accent2)"/>
      </linearGradient></defs>
      <circle cx="6" cy="12" r="2.6"/><circle cx="18" cy="6" r="2.6"/><circle cx="18" cy="18" r="2.6"/>
      <path d="m8.4 10.7 7.2-3.4M8.4 13.3l7.2 3.4"/>
    </svg>
    <span>share<b>·</b>lan</span>
  </div>
  <div class="spacer"></div>
  <button class="chip" id="codeChip" title="Kode akses — klik buat QR">__CODE__ <small>kode</small></button>
  <button class="iconbtn" id="qrTop" title="QR halaman ini"></button>
  <button class="iconbtn" id="themeBtn" title="Ganti tema"></button>
</header>
<main>
  <div class="bar">
    <nav class="crumbs" id="crumbs"></nav>
    <div class="tools">
      <label class="search">
        <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        <input id="q" type="search" placeholder="Cari di folder ini…" aria-label="Cari">
      </label>
      <button class="iconbtn" id="viewBtn" title="Ganti tampilan"></button>
    </div>
  </div>
  <div class="actions" id="folderActions"></div>
  <div id="content" class="list"></div>
</main>
__UPLOAD__
<div class="drop" id="drop"><div>Lepas di sini buat dikirim</div></div>
<div class="modal" id="modal"><div class="sheet">
  <h3 id="sheetTitle"></h3><p id="sheetSub"></p>
  <div class="qrbox" id="qrbox"></div>
  <div class="url" id="sheetUrl"></div>
  <div class="row2">
    <button id="sheetCopy">Salin link</button>
    <button id="sheetOpen" class="primary">Buka</button>
  </div>
</div></div>
<div class="toasts" id="toasts"></div>
<script>__JS__</script>
</body></html>"""

UPLOAD_BAR = r"""<div class="ub" id="ub" hidden>
  <div class="jobs" id="jobs"></div>
  <div class="inner">
    <label class="btn primary" for="pick">
      <svg viewBox="0 0 24 24"><path d="M12 20V8m0 0-4 4m4-4 4 4M4 4h16"/></svg>Kirim file
    </label>
    <input id="pick" type="file" multiple hidden>
    <div class="hint">Atau seret file ke mana aja di halaman ini</div>
  </div>
</div>"""

ERROR_PAGE = r"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__CODE__</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0c12;
color:#e9ebf5;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
@media(prefers-color-scheme:light){body{background:#f4f6fb;color:#121420}}
div{text-align:center;padding:30px}b{font-size:44px;display:block;opacity:.25;letter-spacing:-.03em}
a{color:#8b7bff}</style>
<div><b>__CODE__</b>__MSG__<p><a href="/">← balik ke daftar file</a></p></div>"""

LOGIN_PAGE = r"""<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark"><title>Masukin kode — share·lan</title>
<style>__CSS__
.wrap{min-height:100vh;display:grid;place-items:center;padding:24px}
.box{width:100%;max-width:340px;text-align:center;background:var(--card);
  border:1px solid var(--line);border-radius:22px;padding:30px 26px;box-shadow:var(--shadow);
  backdrop-filter:blur(14px);animation:pop .3s}
.box h1{margin:14px 0 4px;font-size:19px;letter-spacing:-.02em}
.box p{margin:0 0 20px;color:var(--muted);font-size:13px}
.box input{width:100%;height:58px;text-align:center;font-size:27px;font-weight:650;
  letter-spacing:.42em;text-indent:.42em;border-radius:14px;border:1px solid var(--line);
  background:var(--card2);outline:none;transition:border-color .15s}
.box input:focus{border-color:var(--accent)}
.box button{width:100%;height:46px;margin-top:12px;border-radius:14px;font-weight:650;
  background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff}
.err{color:var(--warn);font-size:13px;margin-top:14px;font-weight:550}
.logo{width:44px;height:44px}</style></head><body>
<div class="bgfx"></div>
<div class="wrap"><form class="box" method="post" action="/login">
  <svg class="logo" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.7"
       stroke-linecap="round" stroke-linejoin="round">
    <circle cx="6" cy="12" r="2.6"/><circle cx="18" cy="6" r="2.6"/><circle cx="18" cy="18" r="2.6"/>
    <path d="m8.4 10.7 7.2-3.4M8.4 13.3l7.2 3.4"/></svg>
  <h1>Masukin kode akses</h1>
  <p>Minta kodenya ke yang lagi bagiin file</p>
  <input name="code" inputmode="numeric" pattern="[0-9]*" autocomplete="off"
         maxlength="12" autofocus aria-label="Kode akses">
  <input type="hidden" name="next" value="__NEXT__">
  <button type="submit">Masuk</button>
  __ERR__
</form></div></body></html>"""


def login_page(err="", nxt="/"):
    return (
        LOGIN_PAGE.replace("__CSS__", CSS)
        .replace("__NEXT__", html.escape(nxt, quote=True))
        .replace("__ERR__", f'<div class="err">{html.escape(err)}</div>' if err else "")
    )


def page_html():
    cfg = {
        "initial": ST.initial_path,
        "code": ST.code,
        "canUpload": bool(ST.upload_dir) and not ST.cfg.read_only,
        "thumbs": HAVE_PIL,
        "singleRoot": ST.single_root,
    }
    title = os.path.basename(next(iter(ST.mounts))) if len(ST.mounts) == 1 else "share·lan"
    return (
        SHELL.replace("__CSS__", CSS)
        .replace("__JS__", JS.replace("__CFG__", json.dumps(cfg)))
        .replace("__TITLE__", html.escape(title))
        .replace("__CODE__", html.escape(ST.code))
        .replace("__UPLOAD__", UPLOAD_BAR)  # selalu ada, disembunyiin lewat JS
    )


# --------------------------------------------------------------------------
# Jaringan: nyari alamat yang beneran kepakai
# --------------------------------------------------------------------------

VPNISH = re.compile(r"^(utun|tun|tap|ppp|ipsec|awdl|llw|bridge|docker|vboxnet|vmnet)")
WIRED = re.compile(r"^(en|eth|wl|wlan)")


def _iface_addrs():
    """[(ip, nama_interface)] dari ifconfig / ip addr. Kosong kalau nggak ada dua-duanya."""
    for cmd in (["ifconfig", "-a"], ["ip", "-4", "-o", "addr"]):
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=4).stdout.decode(
                "utf-8", "replace"
            )
        except Exception:
            continue
        found, iface = [], None
        for line in out.splitlines():
            m = re.match(r"^(\w[\w.:-]*):", line)
            if m:
                iface = m.group(1)
            m2 = re.search(r"\binet (?:addr:)?(\d+\.\d+\.\d+\.\d+)", line)
            if m2:
                name = iface
                m3 = re.match(r"^\d+:\s*(\S+)", line)  # format `ip -o`
                if m3:
                    name = m3.group(1)
                found.append((m2.group(1), name or ""))
        if found:
            return found
    return []


def _route_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # nggak ngirim paket, cuma minta routing table nunjuk interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _score(ip, iface, is_route):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return -999
    if addr.is_loopback or addr.is_link_local:
        return -999
    s = 0
    if ip.startswith("192.168."):
        s += 100
    elif ip.startswith("10."):
        s += 90
    elif addr.is_private:
        s += 80
    else:
        s -= 40
    if iface and VPNISH.match(iface):
        s -= 70
    elif iface and WIRED.match(iface):
        s += 20
    if is_route:
        s += 25
    return s


def find_addresses():
    """[(ip, iface, skor)] urut dari yang paling mungkin kepakai."""
    route = _route_ip()
    seen = {}
    for ip, iface in _iface_addrs():
        seen.setdefault(ip, iface)
    if route:
        seen.setdefault(route, "")
    if not seen:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                seen.setdefault(info[4][0], "")
        except OSError:
            pass
    out = [(ip, ifc, _score(ip, ifc, ip == route)) for ip, ifc in seen.items()]
    out = [x for x in out if x[2] > -900]
    out.sort(key=lambda x: -x[2])
    return out


def local_hostname(port):
    try:
        name = socket.gethostname()
    except OSError:
        return None
    if not name:
        return None
    if not name.endswith(".local"):
        name += ".local"
    try:
        socket.getaddrinfo(name, None, socket.AF_INET)
    except OSError:
        return None
    return f"http://{name}:{port}"


# --------------------------------------------------------------------------
# Banner
# --------------------------------------------------------------------------


def share_url():
    if ST.single_file:
        return f"{ST.base_url}/dl?p={urllib.parse.quote(ST.single_file)}&dl=1&code={ST.code}"
    return f"{ST.base_url}/?code={ST.code}"


def print_banner(reprint=False):
    cfg = ST.cfg
    print()
    if not reprint:
        print(f"  {C.accent('◆')} {C.bold('share·lan')}  {C.dim('— nyala. Ctrl-C buat matiin.')}")
        print()
        if not ST.mounts:
            print(
                f"  {C.warn('Belum ada yang dibagikan')} {C.dim('— nggak ada file yang kebuka.')}"
            )
            print(f"  {C.dim('Seret file/folder dari Finder ke jendela ini, terus Enter.')}")
        else:
            print(f"  {C.dim('Dibagikan:')} {len(ST.mounts)} item")
        for name, full in list(ST.mounts.items())[:12]:
            count, size, cut = dir_stats(full, budget=0.6)
            if os.path.isdir(full):
                extra = f"{count}{'+' if cut else ''} file, {human(size)}{'+' if cut else ''}"
                print(f"    {C.accent('•')} {name}/  {C.dim('(' + extra + ')')}")
            else:
                print(f"    {C.accent('•')} {name}  {C.dim('(' + human(size) + ')')}")
        if len(ST.mounts) > 12:
            print(f"    {C.dim('… dan ' + str(len(ST.mounts) - 12) + ' lagi')}")
        print()
    print(f"  {C.dim('Alamat')} : {C.bold(ST.base_url)}   {C.dim('← dipakai di QR')}")
    alt = local_hostname(cfg.port)
    if alt:
        print(f"  {C.dim('Juga')}   : {alt}  {C.dim('(tahan ganti IP)')}")
    print(f"  {C.dim('Kode')}   : {C.bold(C.accent(ST.code))}")
    if not cfg.no_qr:
        print()
        print(qr_ascii(share_url()))
    print()
    print(
        f"  {C.dim('→ Ke HP')}    : scan QR di atas"
        + (f" {C.dim('(langsung download filenya)')}" if ST.single_file else "")
    )
    print(f"  {C.dim('→ Ke laptop')}: kasih alamat + kode di atas")
    others = [x for x in ST.addresses[1:] if x[2] > -900][:3]
    if others:
        alts = ", ".join(f"{ip}{' (' + ifc + ')' if ifc else ''}" for ip, ifc, _ in others)
        print()
        print(f"  {C.warn('⚠')} {C.dim('Nggak kebuka dari HP? Coba alamat lain:')} {alts}")
    print()


# --------------------------------------------------------------------------
# Konsol: nambah/cabut path sambil server jalan
# --------------------------------------------------------------------------

CONSOLE_HELP = """
  Seret file/folder dari Finder ke jendela ini terus Enter buat nambahin.
  Atau ketik path-nya langsung. Bisa beberapa sekaligus.

    ls          lihat yang lagi dibagikan
    rm <nama>   cabut satu (boleh pakai nomor urutnya, mis. rm 2)
    qr          tampilkan ulang alamat + QR
    q           matiin server
"""


def print_shared():
    if not ST.mounts:
        print(f"\n  {C.dim('Belum ada yang dibagikan.')}\n")
        return
    print()
    for i, (name, full) in enumerate(ST.mounts.items(), 1):
        count, size, cut = dir_stats(full, budget=0.4)
        if os.path.isdir(full):
            info = f"{count}{'+' if cut else ''} file, {human(size)}{'+' if cut else ''}"
        else:
            info = human(size)
        print(f"  {C.dim(str(i) + '.')} {name}  {C.dim('(' + info + ')')}")
    print()


def console_available():
    """Aman baca stdin? Proses yang lagi di background nggak boleh baca dari terminal -
    bisa kena SIGTTIN dan malah ngestop sendiri. Pipe/file aman-aman aja."""
    if not sys.stdin or sys.stdin.closed:
        return False
    try:
        if sys.stdin.isatty():
            return os.getpgrp() == os.tcgetpgrp(sys.stdin.fileno())
    except OSError:
        return False
    return True


def console_loop(httpd):
    """Baca perintah dari stdin sambil server jalan."""
    while True:
        # readline(), bukan `for x in sys.stdin` - iterasi file object nge-buffer
        # dan barisnya bisa nyangkut sampai buffer penuh.
        raw = sys.stdin.readline()
        if not raw:
            return  # stdin ketutup: server tetap jalan, cuma nggak bisa diperintah
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        try:
            if low in ("q", "quit", "exit"):
                httpd.shutdown()
                return
            if low in ("?", "h", "help"):
                print(CONSOLE_HELP)
            elif low == "ls":
                print_shared()
            elif low == "qr":
                print_banner(reprint=True)
            elif low.startswith("rm "):
                name = remove_mount(line[3:].strip())
                if name:
                    print(f"  {C.warn('−')} {name} {C.dim('dicabut')}")
                    print_shared()
                else:
                    print(f"  {C.bad('✗')} Nggak ketemu: {line[3:].strip()}")
            else:
                try:
                    tokens = shlex.split(line)
                except ValueError:
                    tokens = [line]  # kutip nggak seimbang: anggap satu path apa adanya
                added, skipped, bad = add_paths(tokens)
                for name, full in added:
                    count, size, _ = dir_stats(full, budget=0.4)
                    info = f"{count} file, {human(size)}" if os.path.isdir(full) else human(size)
                    print(f"  {C.ok('+')} {name}  {C.dim('(' + info + ')')}")
                for raw_path in skipped:
                    print(f"  {C.dim('·')} {C.dim(raw_path + ' udah dibagikan')}")
                for raw_path, why in bad:
                    print(f"  {C.bad('✗')} {raw_path}  {C.dim('(' + why + ')')}")
                if added:
                    total = len(ST.mounts)
                    print(
                        f"  {C.dim(f'Sekarang {total} item dibagikan. Yang lagi buka halamannya')}"
                    )
                    print(f"  {C.dim('bakal lihat sendiri dalam beberapa detik.')}")
        except Exception as e:  # konsol nggak boleh sampai matiin server
            print(f"  {C.bad('✗')} {e}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def bind_server(host, port):
    """Coba port yang diminta, loncat kalau kepake."""
    last = None
    for candidate in range(port, port + 21):
        try:
            return Server((host, candidate), Handler), candidate
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
            last = e
    die(f"Port {port}–{port + 20} kepake semua ({last}). Coba --port lain.")


def keep_awake():
    if sys.platform != "darwin" or ST.cfg.no_caffeinate:
        return None
    try:
        return subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="lanshare",
        description="Bagi file ke satu jaringan. Seret file/folder ke terminal buat nempelin path-nya.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="contoh:\n"
        "  ./lanshare.py                        # nyala kosong, path dilempar belakangan\n"
        "  ./lanshare.py ~/Desktop/video.mp4\n"
        "  ./lanshare.py ~/Documents ~/Foto\\ Liburan/ laporan.pdf\n"
        "  ./lanshare.py . --read-only --code 1234\n"
        "\n"
        "Sambil server jalan, seret file/folder ke terminal + Enter buat nambahin.\n"
        "Ketik ? di situ buat lihat perintah lain (ls, rm, qr, q).\n",
    )
    ap.add_argument(
        "paths",
        nargs="*",
        default=[],
        help="file/folder yang mau dibagikan (wajib disebutin; pakai '.' buat folder sekarang)",
    )
    ap.add_argument(
        "--port", type=int, default=8000, help="port (default 8000, loncat kalau kepake)"
    )
    ap.add_argument("--host", default="0.0.0.0", help="alamat bind (default 0.0.0.0)")
    ap.add_argument("--ip", help="paksa alamat yang ditampilin di QR")
    ap.add_argument("--code", help="kode akses sendiri (default: acak)")
    ap.add_argument("--code-len", type=int, default=4, help="panjang kode acak (default 4)")
    ap.add_argument(
        "--upload-to", help="folder tujuan upload (default: folder pertama yang dibagikan)"
    )
    ap.add_argument(
        "--max-upload", type=parse_size, default=0, help="batas ukuran per upload, mis. 2G"
    )
    ap.add_argument("--read-only", action="store_true", help="matiin upload")
    ap.add_argument("--hidden", action="store_true", help="ikutin file/folder tersembunyi")
    ap.add_argument("--no-qr", action="store_true", help="jangan cetak QR di terminal")
    ap.add_argument(
        "--zip-resume-limit",
        type=parse_size,
        default=20 << 30,
        help="ZIP di bawah ukuran ini dibikin bisa di-resume (default 20G)",
    )
    ap.add_argument("--no-caffeinate", action="store_true", help="jangan cegah Mac ketiduran")
    return ap.parse_args(argv)


def configure(cfg):
    """Isi ST dari argumen CLI. Dipisah dari main() biar bisa dites."""
    ST.cfg = cfg
    if cfg.code_len < 3 or cfg.code_len > 12:
        die("--code-len harus antara 3 dan 12.")
    ST.code = str(cfg.code) if cfg.code else new_code(cfg.code_len)
    if not ST.code.strip():
        die("--code nggak boleh kosong.")

    ST.mounts = build_mounts(cfg.paths)
    if cfg.upload_to:
        up = os.path.realpath(os.path.expanduser(cfg.upload_to))
        if not os.path.isdir(up):
            die(f"--upload-to bukan folder: {cfg.upload_to}")
        ST.upload_dir = up
    recompute()
    ST.addresses = find_addresses()
    return ST


def main(argv=None):
    try:
        sys.stdout.reconfigure(line_buffering=True)  # banner langsung nongol walau di-pipe
    except Exception:
        pass
    cfg = parse_args(argv)
    configure(cfg)

    if cfg.ip:
        ip = cfg.ip
    elif ST.addresses:
        ip = ST.addresses[0][0]
    else:
        ip = "127.0.0.1"

    httpd, port = bind_server(cfg.host, cfg.port)
    cfg.port = port
    ST.base_url = f"http://{ip}:{port}"

    awake = keep_awake()
    print_banner()
    if cfg.read_only:
        print(f"  {C.dim('Mode baca-saja: upload dimatiin.')}\n")
    elif not ST.upload_dir:
        print(f"  {C.dim('Nggak ada folder yang bisa ditulis, jadi upload dimatiin.')}\n")

    if console_available():
        print(f"  {C.dim('Seret file ke sini + Enter buat nambahin. Ketik ? buat bantuan.')}\n")
        threading.Thread(target=console_loop, args=(httpd,), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n  {C.ok('✓')} Server dimatiin. Akses langsung putus.\n")
    finally:
        httpd.server_close()
        if awake:
            awake.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
