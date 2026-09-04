"""Tes keamanan path: apa pun yang dikirim penerima nggak boleh nembus keluar mount."""

import argparse
import os

import pytest

import lanshare as L


@pytest.fixture
def mounted(tree):
    L.ST.cfg = argparse.Namespace(hidden=False)
    L.ST.mounts = L.build_mounts([str(tree / "Dokumen"), str(tree / "klip.mp4")])
    return tree


@pytest.mark.parametrize(
    "p",
    [
        "Dokumen/../../etc/passwd",
        "Dokumen/../../../../../../etc/passwd",
        "Dokumen/..",
        "Dokumen/arsip/../../..",
        "Dokumen/./../..",
    ],
)
def test_tolak_yang_keluar_mount(mounted, p):
    with pytest.raises(L.Denied):
        L.resolve(p)


@pytest.mark.parametrize("p", ["/etc/passwd", "gaib", "gaib/apa", "etc"])
def test_mount_yang_gak_ada(mounted, p):
    with pytest.raises(L.Missing):
        L.resolve(p)


def test_symlink_yang_nunjuk_keluar_ditolak(mounted):
    link = mounted / "Dokumen" / "tembus"
    os.symlink("/etc", link)
    with pytest.raises(L.Denied):
        L.resolve("Dokumen/tembus/passwd")


def test_path_wajar_tetap_jalan(mounted):
    assert L.resolve("") is None  # root virtual = daftar mount
    assert L.resolve("Dokumen") == str(mounted / "Dokumen")
    assert L.resolve("Dokumen/arsip/kopi ☕.txt") == str(mounted / "Dokumen/arsip/kopi ☕.txt")
    assert L.resolve("/Dokumen/") == str(mounted / "Dokumen")


def test_dotfile_disembunyiin_secara_default(mounted):
    names = [e["name"] for e in L.list_entries("Dokumen", L.resolve("Dokumen"))]
    assert ".rahasia" not in names
    assert "catatan.txt" in names


def test_hidden_bisa_dinyalain(mounted):
    L.ST.cfg = argparse.Namespace(hidden=True)
    names = [e["name"] for e in L.list_entries("Dokumen", L.resolve("Dokumen"))]
    assert ".rahasia" in names


def test_listing_folder_duluan_baru_file(mounted):
    entries = L.list_entries("Dokumen", L.resolve("Dokumen"))
    kinds = [e["dir"] for e in entries]
    assert kinds == sorted(kinds, reverse=True)


def test_file_part_gak_ikut_kelisting(mounted):
    (mounted / "Dokumen" / "lagi-upload.part").write_text("belum kelar")
    names = [e["name"] for e in L.list_entries("Dokumen", L.resolve("Dokumen"))]
    assert "lagi-upload.part" not in names
