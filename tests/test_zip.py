"""Tes ZIP: layout byte harus persis, dan potong-potongnya harus nyambung mulus.
Ini yang bikin ZIP bisa di-resume, jadi paling detail dites di sini."""

import argparse
import io
import zipfile

import pytest

import lanshare as L


@pytest.fixture
def plan(tree):
    L.ST.cfg = argparse.Namespace(hidden=False)
    L.ST.crc_cache.clear()
    files = list(L.walk_files(str(tree / "Dokumen"), prefix=""))
    segments, total = L.build_zip_plan(files)
    return segments, total, tree


def emit(segments, lo, hi):
    buf = []

    def put_file(path, off, n, size, mtime):
        with open(path, "rb") as f:
            f.seek(off)
            buf.append(f.read(n))

    L.emit_plan(segments, lo, hi, buf.append, put_file)
    return b"".join(buf)


def test_ukuran_terhitung_sama_dengan_byte_yang_keluar(plan):
    segments, total, _ = plan
    assert len(emit(segments, 0, total)) == total


def test_zip_valid_dan_isinya_bener(plan):
    segments, total, _ = plan
    zf = zipfile.ZipFile(io.BytesIO(emit(segments, 0, total)))
    assert zf.testzip() is None
    assert sorted(zf.namelist()) == ["arsip/kopi ☕.txt", "catatan.txt", "data.bin"]
    assert zf.read("arsip/kopi ☕.txt") == b"unicode ok"


def test_dotfile_gak_ikut_masuk_zip(plan):
    segments, total, _ = plan
    zf = zipfile.ZipFile(io.BytesIO(emit(segments, 0, total)))
    assert not any(n.startswith(".") or "/." in n for n in zf.namelist())


@pytest.mark.parametrize("step", [1, 7, 999, 4096, 65536])
def test_hasil_potong_potong_identik(plan, step):
    """Inti fitur resume: ambil byte per potongan harus sama persis dengan sekali ambil."""
    segments, total, _ = plan
    utuh = emit(segments, 0, total)
    sambung = b"".join(emit(segments, lo, min(lo + step, total)) for lo in range(0, total, step))
    assert sambung == utuh


def test_ambil_dari_tengah(plan):
    segments, total, _ = plan
    utuh = emit(segments, 0, total)
    tengah = total // 3
    assert emit(segments, tengah, total) == utuh[tengah:]


def test_plan_stabil_kalau_dipanggil_ulang(plan, tree):
    """Dipanggil dua kali harus ngasih byte identik - kalau nggak, resume bakal korup."""
    segments, total, _ = plan
    files = list(L.walk_files(str(tree / "Dokumen"), prefix=""))
    segments2, total2 = L.build_zip_plan(files)
    assert total2 == total
    assert emit(segments2, 0, total2) == emit(segments, 0, total)


def test_crc_dicache(tree):
    L.ST.cfg = argparse.Namespace(hidden=False)
    L.ST.crc_cache.clear()
    path = str(tree / "Dokumen" / "data.bin")
    assert L.crc32_of(path) == L.crc32_of(path)
    assert len(L.ST.crc_cache) == 1


def test_zip_semua_mount_diprefiks_nama_mount(tree):
    L.ST.cfg = argparse.Namespace(hidden=False)
    L.ST.mounts = L.build_mounts([str(tree / "Dokumen"), str(tree / "klip.mp4")])
    files = list(L.zip_entries("", None))
    arcs = sorted(a for _, a in files)
    assert arcs[0].startswith("Dokumen/")
    assert "klip.mp4" in arcs
