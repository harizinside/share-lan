import hashlib
import io
import os
import zlib

from .state import CHUNK, IMAGE_EXT, ST, THUMB_CACHE_MAX

try:
    from PIL import Image, ImageOps

    HAVE_PIL = True
except Exception:  # Pillow opsional - tanpa dia thumbnail mati, sisanya jalan
    HAVE_PIL = False


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
    """JPEG kecil buat preview. None kalau nggak bisa - UI-nya mundur ke ikon."""
    if not HAVE_PIL:
        return None
    if os.path.splitext(path)[1].lower() not in IMAGE_EXT:
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
    with ST.lock:
        if key not in ST.thumb_cache and len(ST.thumb_cache) >= THUMB_CACHE_MAX:
            oldest = next(iter(ST.thumb_cache))
            del ST.thumb_cache[oldest]
        ST.thumb_cache[key] = data
    return data
