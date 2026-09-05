"""ZIP tests: the byte layout must be exact, and chunked reads must line up seamlessly.
This is what makes ZIP resumable, so it's tested in the most detail here."""

import argparse
import io
import zipfile

import pytest

import lanshare as L


@pytest.fixture
def plan(tree):
    L.ST.cfg = argparse.Namespace(hidden=False)
    L.ST.crc_cache.clear()
    files = list(L.walk_files(str(tree / "Documents"), prefix=""))
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


def test_computed_size_matches_bytes_emitted(plan):
    segments, total, _ = plan
    assert len(emit(segments, 0, total)) == total


def test_zip_is_valid_and_contents_correct(plan):
    segments, total, _ = plan
    zf = zipfile.ZipFile(io.BytesIO(emit(segments, 0, total)))
    assert zf.testzip() is None
    assert sorted(zf.namelist()) == ["archive/coffee ☕.txt", "data.bin", "notes.txt"]
    assert zf.read("archive/coffee ☕.txt") == b"unicode ok"


def test_dotfiles_excluded_from_zip(plan):
    segments, total, _ = plan
    zf = zipfile.ZipFile(io.BytesIO(emit(segments, 0, total)))
    assert not any(n.startswith(".") or "/." in n for n in zf.namelist())


@pytest.mark.parametrize("step", [1, 7, 999, 4096, 65536])
def test_chunked_output_is_identical(plan, step):
    """The core of the resume feature: fetching in chunks must exactly match one big fetch."""
    segments, total, _ = plan
    whole = emit(segments, 0, total)
    joined = b"".join(emit(segments, lo, min(lo + step, total)) for lo in range(0, total, step))
    assert joined == whole


def test_resume_from_middle(plan):
    segments, total, _ = plan
    whole = emit(segments, 0, total)
    middle = total // 3
    assert emit(segments, middle, total) == whole[middle:]


def test_plan_is_stable_across_calls(plan, tree):
    """Calling it twice must give identical bytes - otherwise resume would produce corruption."""
    segments, total, _ = plan
    files = list(L.walk_files(str(tree / "Documents"), prefix=""))
    segments2, total2 = L.build_zip_plan(files)
    assert total2 == total
    assert emit(segments2, 0, total2) == emit(segments, 0, total)


def test_crc_is_cached(tree):
    L.ST.cfg = argparse.Namespace(hidden=False)
    L.ST.crc_cache.clear()
    path = str(tree / "Documents" / "data.bin")
    assert L.crc32_of(path) == L.crc32_of(path)
    assert len(L.ST.crc_cache) == 1


def test_zip_all_mounts_prefixed_by_mount_name(tree):
    L.ST.cfg = argparse.Namespace(hidden=False)
    L.ST.mounts = L.build_mounts([str(tree / "Documents"), str(tree / "clip.mp4")])
    files = list(L.zip_entries("", None))
    arcs = sorted(a for _, a in files)
    assert arcs[0].startswith("Documents/")
    assert "clip.mp4" in arcs
