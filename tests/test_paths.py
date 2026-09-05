"""Path security tests: nothing a recipient sends should ever escape a mount."""

import argparse
import os

import pytest

import lanshare as L


@pytest.fixture
def mounted(tree):
    L.ST.cfg = argparse.Namespace(hidden=False)
    L.ST.mounts = L.build_mounts([str(tree / "Documents"), str(tree / "clip.mp4")])
    return tree


@pytest.mark.parametrize(
    "p",
    [
        "Documents/../../etc/passwd",
        "Documents/../../../../../../etc/passwd",
        "Documents/..",
        "Documents/archive/../../..",
        "Documents/./../..",
    ],
)
def test_rejects_path_outside_mount(mounted, p):
    with pytest.raises(L.Denied):
        L.resolve(p)


@pytest.mark.parametrize("p", ["/etc/passwd", "ghost", "ghost/whatever", "etc"])
def test_unknown_mount(mounted, p):
    with pytest.raises(L.Missing):
        L.resolve(p)


def test_symlink_pointing_outside_rejected(mounted):
    link = mounted / "Documents" / "escape"
    os.symlink("/etc", link)
    with pytest.raises(L.Denied):
        L.resolve("Documents/escape/passwd")


def test_normal_path_still_works(mounted):
    assert L.resolve("") is None  # virtual root = the mount list
    assert L.resolve("Documents") == str(mounted / "Documents")
    assert L.resolve("Documents/archive/coffee ☕.txt") == str(
        mounted / "Documents/archive/coffee ☕.txt"
    )
    assert L.resolve("/Documents/") == str(mounted / "Documents")


def test_dotfiles_hidden_by_default(mounted):
    names = [e["name"] for e in L.list_entries("Documents", L.resolve("Documents"))]
    assert ".secret" not in names
    assert "notes.txt" in names


def test_hidden_can_be_enabled(mounted):
    L.ST.cfg = argparse.Namespace(hidden=True)
    names = [e["name"] for e in L.list_entries("Documents", L.resolve("Documents"))]
    assert ".secret" in names


def test_listing_folders_before_files(mounted):
    entries = L.list_entries("Documents", L.resolve("Documents"))
    kinds = [e["dir"] for e in entries]
    assert kinds == sorted(kinds, reverse=True)


def test_part_files_excluded_from_listing(mounted):
    (mounted / "Documents" / "in-progress.part").write_text("not finished yet")
    names = [e["name"] for e in L.list_entries("Documents", L.resolve("Documents"))]
    assert "in-progress.part" not in names
