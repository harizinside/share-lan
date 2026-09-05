import hashlib
import io
import os
import threading
import zlib

from .state import CHUNK, IMAGE_EXT, ST, THUMB_CACHE_MAX

try:
    from PIL import Image, ImageOps

    HAVE_PIL = True
except Exception:  # Pillow opsional - tanpa dia thumbnail mati, sisanya jalan
    HAVE_PIL = False

try:
    import pypdfium2 as pdfium

    HAVE_PDF = True
except ImportError:
    HAVE_PDF = False

# PDFium tidak thread-safe, termasuk saat membuka/menutup dokumen.
_pdf_lock = threading.Lock()

SVG_THUMB_MAX = 256 * 1024  # SVG lebih besar dari ini nggak dikirim sebagai thumb


def _pdf_thumb(path, box):
    with _pdf_lock, pdfium.PdfDocument(path) as doc:
        page = doc[0]
        try:
            width, height = page.get_size()
            scale = box / max(width, height / 2)
            bitmap = page.render(scale=scale, crop=(0, height / 2, 0, 0))
            try:
                with bitmap.to_pil() as im:
                    buf = io.BytesIO()
                    im.convert("RGB").save(buf, "JPEG", quality=82, optimize=True)
                    return buf.getvalue()
            finally:
                bitmap.close()
        finally:
            page.close()


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


def make_thumb(path, box=360):
    """(bytes, content_type) kecil buat preview. None kalau nggak bisa - UI mundur ke ikon."""
    if not HAVE_PIL:
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext not in IMAGE_EXT and ext != ".pdf":
        return None
    try:
        key = file_key(path) + (box,)
    except OSError:
        return None
    with ST.lock:
        hit = ST.thumb_cache.get(key)
    if hit is not None:
        return hit
    try:
        if ext == ".pdf":
            data, ctype = _pdf_thumb(path, box), "image/jpeg"
        elif ext == ".svg":
            data, ctype = _svg_thumb(path)
        else:
            data, ctype = _image_thumb(path, box), "image/jpeg"
    except Exception:
        return None
    with ST.lock:
        if key not in ST.thumb_cache and len(ST.thumb_cache) >= THUMB_CACHE_MAX:
            oldest = next(iter(ST.thumb_cache))
            del ST.thumb_cache[oldest]
        ST.thumb_cache[key] = (data, ctype)
    return data, ctype


def _image_thumb(path, box):
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((box, box))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True)
        return buf.getvalue()


def _svg_thumb(path):
    """SVG-nya sendiri jadi thumbnail - browser merendernya native di <img>."""
    if os.path.getsize(path) > SVG_THUMB_MAX:
        raise ValueError("SVG kegedean buat dijadiin thumb")
    with open(path, "rb") as f:
        data = f.read()
    if b"<svg" not in data:
        raise ValueError("bukan SVG")
    return data, "image/svg+xml"
