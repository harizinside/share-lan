"""Document preview in the browser - GroupDocs.Viewer when installed, a fallback when not.

`groupdocs-viewer-net` (GroupDocs.Viewer for Python via .NET) is an optional dependency,
following the same pattern as Pillow: if it's missing, the /preview endpoint falls back
to the browser's built-in viewer (PDF, images, media, plain text) and everything else
keeps working normally.

Each document page is rendered to HTML with embedded resources, so a single HTML
response can be sent to the iframe without needing to serve separate resource files.
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

# The native renderer is heavy and doesn't claim thread safety, so calls are serialized.
_lock = threading.Lock()
PREVIEW_CACHE_MAX = 16

# Subset of GroupDocs.Viewer's 190+ formats that's relevant for file sharing.
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
    """The document couldn't be rendered (corrupt, unrecognized format, library error)."""


def previewable(name):
    return os.path.splitext(name)[1].lower() in DOC_EXT


def render_html(path):
    """Render a document to (combined HTML bytes, page count).

    Results are cached per (path, size, mtime) - the same file isn't re-rendered.
    Raises RenderError on failure.
    """
    try:
        key = file_key(path) + ("gdocs",)
    except OSError as e:
        raise RenderError(f"couldn't read file: {e}") from e
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
            raise RenderError("render produced no pages")
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
