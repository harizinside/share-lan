# Di bawah ambang: CRC dihitung duluan, jadi seluruh layout byte bisa dipetakan
# persis -> Content-Length ketauan -> Range jalan -> ZIP-nya bisa di-resume.
# Di atas ambang: streaming biasa, cepat mulai tapi nggak bisa dilanjut.

import os
import struct
import time
import zipfile

from .mounts import walk_files
from .state import ST, ZIP64_LIMIT
from .thumb import crc32_of


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
