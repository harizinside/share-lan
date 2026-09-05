"""Preview dokumen di browser - GroupDocs.Viewer kalau kepasang, fallback kalau nggak.

`groupdocs-viewer-net` (GroupDocs.Viewer for Python via .NET) itu dependency opsional,
pola sama kayak Pillow: kalau nggak ada, endpoint /preview mundur ke viewer bawaan
browser (PDF, gambar, media, teks polos) dan sisanya tetap jalan normal.

Tiap halaman dokumen di-render jadi HTML dengan resource ter-embed, jadi cukup satu
respons HTML dikirim ke iframe tanpa perlu serve file resource terpisah.
"""

import os
import shutil
import tempfile
import threading

from .state import ST
from .thumb import file_key

try:
    from groupdocs.viewer import Viewer
    from groupdocs.viewer.options import HtmlViewOptions

    HAVE_GROUPDOCS = True
except Exception:
    HAVE_GROUPDOCS = False

# Render native-nya berat + thread safety-nya nggak diklaim, jadi diserialisasi.
_lock = threading.Lock()
PREVIEW_CACHE_MAX = 16

# Subset dari 190+ format GroupDocs.Viewer yang relevan buat file sharing.
DOC_EXT = {
    ".csv",
    ".doc",
    ".docm",
    ".docx",
    ".dot",
    ".dotm",
    ".dotx",
    ".dwg",
    ".dxf",
    ".eml",
    ".emlx",
    ".epub",
    ".mobi",
    ".msg",
    ".odp",
    ".ods",
    ".odt",
    ".one",
    ".otp",
    ".ots",
    ".ott",
    ".pot",
    ".potm",
    ".potx",
    ".pps",
    ".ppsx",
    ".ppt",
    ".pptm",
    ".pptx",
    ".rtf",
    ".vsd",
    ".vsdx",
    ".xls",
    ".xlsb",
    ".xlsm",
    ".xlsx",
    ".xlt",
    ".xltx",
}


class RenderError(Exception):
    """Dokumen nggak bisa di-render (rusak, format nggak dikenal, error library)."""


def previewable(name):
    return os.path.splitext(name)[1].lower() in DOC_EXT


def render_html(path):
    """Render dokumen jadi (bytes HTML gabungan, jumlah halaman).

    Hasilnya di-cache per (path, size, mtime) - file yang sama nggak di-render ulang.
    Raise RenderError kalau gagal.
    """
    try:
        key = file_key(path) + ("gdocs",)
    except OSError as e:
        raise RenderError(f"file nggak kebaca: {e}") from e
    with ST.lock:
        hit = ST.preview_cache.get(key)
    if hit is not None:
        return hit
    with _lock:
        with ST.lock:
            hit = ST.preview_cache.get(key)
        if hit is not None:
            return hit
        data = _render_to_html(path)
        with ST.lock:
            if key not in ST.preview_cache and len(ST.preview_cache) >= PREVIEW_CACHE_MAX:
                del ST.preview_cache[next(iter(ST.preview_cache))]
            ST.preview_cache[key] = data
    return data


def _render_to_html(path):
    outdir = tempfile.mkdtemp(prefix="lanshare-view-")
    try:
        with Viewer(path) as viewer:
            viewer.view(
                HtmlViewOptions.for_embedded_resources(os.path.join(outdir, "page_{0}.html"))
            )
        pages = sorted(f for f in os.listdir(outdir) if f.endswith(".html"))
        if not pages:
            raise RenderError("render nggak ngasilin halaman")
        chunks = []
        for name in pages:
            with open(os.path.join(outdir, name), "rb") as f:
                chunks.append(f.read())
        return b"".join(chunks), len(pages)
    except RenderError:
        raise
    except Exception as e:
        raise RenderError(str(e)) from e
    finally:
        shutil.rmtree(outdir, ignore_errors=True)
