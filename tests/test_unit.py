"""Tes fungsi-fungsi murni: format, sanitasi nama, parsing Range, penamaan mount."""

import argparse
import os

import pytest

import lanshare as L


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "0 B"),
        (999, "999 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1 << 20, "1.0 MB"),
        (5 * (1 << 30), "5.0 GB"),
    ],
)
def test_human(n, expected):
    assert L.human(n) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("500", 500),
        ("10K", 10 << 10),
        ("2M", 2 << 20),
        ("20G", 20 << 30),
        ("1.5G", int(1.5 * (1 << 30))),
        (" 4 g ", 4 << 30),
    ],
)
def test_parse_size(text, expected):
    assert L.parse_size(text) == expected


def test_parse_size_menolak_yang_ngaco():
    with pytest.raises(argparse.ArgumentTypeError):
        L.parse_size("banyak banget")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../evil.sh", "evil.sh"),  # komponen path dibuang
        ("foo/bar.txt", "bar.txt"),
        ("..\\..\\win.exe", "win.exe"),  # separator Windows juga
        ("CON", "_CON"),  # nama cadangan Windows
        ("nul.txt", "_nul.txt"),
        ("....", "upload"),  # titik doang -> nama cadangan
        ("", "upload"),
        ("  spasi  .txt", "spasi .txt"),
    ],
)
def test_safe_upload_name(raw, expected):
    assert L.safe_upload_name(raw) == expected


def test_safe_upload_name_buang_karakter_kontrol():
    assert "\x00" not in L.safe_upload_name("a\x00b.txt")
    assert "\n" not in L.safe_upload_name("a\nb.txt")


def test_safe_upload_name_batasi_panjang():
    hasil = L.safe_upload_name("A" * 500 + ".txt")
    assert len(os.path.splitext(hasil)[0]) <= 200


def test_safe_upload_name_hindari_bentrok_part():
    # File .part dipakai buat upload yang belum kelar - nama upload nggak boleh nyamar
    assert not L.safe_upload_name("nyamar.part").endswith(".part")


@pytest.mark.parametrize(
    "header,size,expected",
    [
        ("bytes=0-99", 1000, (0, 99)),
        ("bytes=500-", 1000, (500, 999)),
        ("bytes=-100", 1000, (900, 999)),
        ("bytes=0-99999", 1000, (0, 999)),  # dipotong ke ukuran file
        ("bytes=0-99,200-299", 1000, "full"),  # multi-range -> kirim penuh
        ("bytes=abc", 1000, "full"),
        ("bytes=2000-", 1000, "unsat"),  # lewat ujung file
        (None, 1000, None),
        ("kambing=0-9", 1000, None),
    ],
)
def test_parse_range(header, size, expected):
    assert L.parse_range(header, size) == expected


def test_unique_name_bikin_basename_kembar_beda():
    taken = {"laporan.pdf": 1}
    assert L.unique_name("laporan.pdf", taken) == "laporan (2).pdf"
    taken["laporan (2).pdf"] = 1
    assert L.unique_name("laporan.pdf", taken) == "laporan (3).pdf"


def test_build_mounts_pakai_basename_bukan_path_asli(tree):
    mounts = L.build_mounts([str(tree / "Dokumen"), str(tree / "klip.mp4")])
    assert list(mounts) == ["Dokumen", "klip.mp4"]
    assert mounts["klip.mp4"] == str(tree / "klip.mp4")


def test_build_mounts_bedain_basename_kembar(tree):
    mounts = L.build_mounts(
        [str(tree / "Dokumen" / "catatan.txt"), str(tree / "lain" / "catatan.txt")]
    )
    assert list(mounts) == ["catatan.txt", "catatan (2).txt"]
    # yang penting isinya nggak ketuker
    assert open(mounts["catatan (2).txt"]).read() == "versi lain"


def test_build_mounts_nolak_path_ngaco(tree, capsys):
    with pytest.raises(SystemExit):
        L.build_mounts([str(tree / "gaib")])
    assert "gaib" in capsys.readouterr().err


@pytest.mark.parametrize(
    "name,expected",
    [
        ("a.jpg", "image"),
        ("a.MP4", "video"),
        ("a.mp3", "audio"),
        ("a.zip", "archive"),
        ("a.pdf", "doc"),
        ("a.py", "code"),
        ("a.xyz", "file"),
    ],
)
def test_kind_of(name, expected):
    assert L.kind_of(name, False) == expected
    assert L.kind_of(name, True) == "dir"


def test_tanpa_argumen_nggak_ngebagiin_apa_apa():
    """Jalan kosong itu aman: server nyala tapi nol file kebuka. Yang bahaya itu
    diam-diam default ke folder aktif - source code bisa kebagi tanpa disadari."""
    cfg = L.parse_args([])
    assert cfg.paths == []
    L.configure(cfg)
    assert L.ST.mounts == {}
    assert L.ST.upload_dir is None
    assert L.ST.initial_path == ""


def test_nambah_dan_nyabut_path_sambil_jalan(tree):
    L.configure(L.parse_args([]))
    rev_awal = L.ST.revision

    added, skipped, bad = L.add_paths([str(tree / "klip.mp4")])
    assert [n for n, _ in added] == ["klip.mp4"]
    assert L.ST.mounts["klip.mp4"] == str(tree / "klip.mp4")
    assert L.ST.revision > rev_awal  # klien polling ini buat tau ada perubahan

    # path yang sama nggak didobelin
    assert L.add_paths([str(tree / "klip.mp4")])[1] == [str(tree / "klip.mp4")]
    assert len(L.ST.mounts) == 1

    # path ngaco dilaporin, bukan bikin server mati
    assert L.add_paths([str(tree / "gaib.txt")])[2][0][1] == "nggak ada"

    assert L.remove_mount("klip.mp4") == "klip.mp4"
    assert L.ST.mounts == {}


def test_nyabut_pakai_nomor_urut(tree):
    L.configure(L.parse_args([str(tree / "Dokumen"), str(tree / "klip.mp4")]))
    assert L.remove_mount("2") == "klip.mp4"
    assert list(L.ST.mounts) == ["Dokumen"]


def test_nambah_folder_nyalain_upload(tree):
    L.configure(L.parse_args([str(tree / "klip.mp4")]))
    assert L.ST.upload_dir is None  # cuma file: nggak ada tempat naro upload
    L.add_paths([str(tree / "lain")])
    assert L.ST.upload_dir == str(tree / "lain")


def test_titik_eksplisit_tetap_boleh(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("hai")
    monkeypatch.chdir(tmp_path)
    mounts = L.build_mounts(["."])
    assert list(mounts) == [tmp_path.name]
