"""Tests for pure functions: formatting, name sanitization, Range parsing, mount naming."""

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


def test_parse_size_rejects_garbage():
    with pytest.raises(argparse.ArgumentTypeError):
        L.parse_size("a whole lot")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../evil.sh", "evil.sh"),  # path components stripped
        ("foo/bar.txt", "bar.txt"),
        ("..\\..\\win.exe", "win.exe"),  # Windows separators too
        ("CON", "_CON"),  # Windows reserved name
        ("nul.txt", "_nul.txt"),
        ("....", "upload"),  # dots only -> fallback name
        ("", "upload"),
        ("  space  .txt", "space .txt"),
    ],
)
def test_safe_upload_name(raw, expected):
    assert L.safe_upload_name(raw) == expected


def test_safe_upload_name_strips_control_chars():
    assert "\x00" not in L.safe_upload_name("a\x00b.txt")
    assert "\n" not in L.safe_upload_name("a\nb.txt")


def test_safe_upload_name_limits_length():
    result = L.safe_upload_name("A" * 500 + ".txt")
    assert len(os.path.splitext(result)[0]) <= 200


def test_safe_upload_name_avoids_part_collision():
    # .part files are used for in-progress uploads - an upload name mustn't impersonate one
    assert not L.safe_upload_name("disguise.part").endswith(".part")


@pytest.mark.parametrize(
    "header,size,expected",
    [
        ("bytes=0-99", 1000, (0, 99)),
        ("bytes=500-", 1000, (500, 999)),
        ("bytes=-100", 1000, (900, 999)),
        ("bytes=0-99999", 1000, (0, 999)),  # clamped to the file size
        ("bytes=0-99,200-299", 1000, "full"),  # multi-range -> send in full
        ("bytes=abc", 1000, "full"),
        ("bytes=2000-", 1000, "unsat"),  # past the end of the file
        (None, 1000, None),
        ("goat=0-9", 1000, None),
    ],
)
def test_parse_range(header, size, expected):
    assert L.parse_range(header, size) == expected


def test_unique_name_differentiates_duplicate_basenames():
    taken = {"report.pdf": 1}
    assert L.unique_name("report.pdf", taken) == "report (2).pdf"
    taken["report (2).pdf"] = 1
    assert L.unique_name("report.pdf", taken) == "report (3).pdf"


def test_build_mounts_uses_basename_not_full_path(tree):
    mounts = L.build_mounts([str(tree / "Documents"), str(tree / "clip.mp4")])
    assert list(mounts) == ["Documents", "clip.mp4"]
    assert mounts["clip.mp4"] == str(tree / "clip.mp4")


def test_build_mounts_differentiates_duplicate_basenames(tree):
    mounts = L.build_mounts(
        [str(tree / "Documents" / "notes.txt"), str(tree / "other" / "notes.txt")]
    )
    assert list(mounts) == ["notes.txt", "notes (2).txt"]
    # what matters is the contents aren't swapped
    assert open(mounts["notes (2).txt"]).read() == "other version"


def test_build_mounts_rejects_bad_path(tree, capsys):
    with pytest.raises(SystemExit):
        L.build_mounts([str(tree / "ghost")])
    assert "ghost" in capsys.readouterr().err


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


def test_no_args_shares_nothing():
    """Running with no args is safe: the server starts but zero files are open. The
    dangerous alternative would be silently defaulting to the current folder - source
    code could end up shared without anyone noticing."""
    cfg = L.parse_args([])
    assert cfg.paths == []
    L.configure(cfg)
    assert L.ST.mounts == {}
    assert L.ST.initial_path == ""


def test_add_and_remove_path_while_running(tree):
    L.configure(L.parse_args([]))
    rev_start = L.ST.revision

    added, skipped, bad = L.add_paths([str(tree / "clip.mp4")])
    assert [n for n, _ in added] == ["clip.mp4"]
    assert L.ST.mounts["clip.mp4"] == str(tree / "clip.mp4")
    assert L.ST.revision > rev_start  # polling clients use this to detect a change

    # the same path isn't added twice
    assert L.add_paths([str(tree / "clip.mp4")])[1] == [str(tree / "clip.mp4")]
    assert len(L.ST.mounts) == 1

    # a bad path is reported, not a reason to bring the server down
    assert L.add_paths([str(tree / "ghost.txt")])[2][0][1] == "not found"

    assert L.remove_mount("clip.mp4") == "clip.mp4"
    assert L.ST.mounts == {}


def test_remove_by_index(tree):
    L.configure(L.parse_args([str(tree / "Documents"), str(tree / "clip.mp4")]))
    assert L.remove_mount("2") == "clip.mp4"
    assert list(L.ST.mounts) == ["Documents"]


def test_can_upload_here_follows_target(tree):
    """There's no global upload folder anymore: whether upload is allowed is decided
    per-folder (from the resolved target), not by a global flag."""
    L.configure(L.parse_args([str(tree / "clip.mp4"), str(tree / "other")]))
    # a mount that's just a file -> not a folder -> can't upload there
    assert L.can_upload_here(L.resolve("clip.mp4")) is False
    # a writable folder -> upload allowed
    assert L.can_upload_here(L.resolve("other")) is True
    # virtual root (multiple mounts) -> no target folder -> upload not allowed
    assert L.can_upload_here(L.resolve(None)) is False


def test_explicit_dot_still_allowed(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("hi")
    monkeypatch.chdir(tmp_path)
    mounts = L.build_mounts(["."])
    assert list(mounts) == [tmp_path.name]
