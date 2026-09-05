import os
import re
import time
import urllib.parse

from .fmt import C, die
from .state import AUDIO_EXT, IMAGE_EXT, ST, VIDEO_EXT, WIN_RESERVED


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
    """Dipanggil tiap daftar bagikan berubah: nentuin bentuk root."""
    items = list(ST.mounts.items())
    ST.single_root = len(items) == 1 and os.path.isdir(items[0][1])
    ST.initial_path = items[0][0] if ST.single_root else ""
    ST.single_file = items[0][0] if len(items) == 1 and os.path.isfile(items[0][1]) else None
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


def can_upload_here(target):
    """Target (hasil resolve) boleh dipakai buat nyimpen upload? None = root virtual."""
    return (
        not ST.cfg.read_only
        and target is not None
        and os.path.isdir(target)
        and os.access(target, os.W_OK)
    )


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
