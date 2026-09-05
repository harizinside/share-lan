"""Tes buat console.py - REPL ls/rm/qr/q yang jalan di stdin."""

import pytest

import lanshare as L
from lanshare.console import CONSOLE_HELP, console_available, console_loop, print_shared


class FakeStdin:
    """readline() ngeluarin baris satu-satu, baris kosong di ujung = stdin ketutup (EOF)."""

    def __init__(self, lines):
        self.lines = list(lines) + [""]
        self.closed = False

    def readline(self):
        return self.lines.pop(0) if self.lines else ""

    def isatty(self):
        return False


class FakeServer:
    def __init__(self):
        self.shutdown_called = False

    def shutdown(self):
        self.shutdown_called = True


@pytest.fixture
def configured(tree):
    """ST kekonfigurasi (mounts + cfg) tanpa nyalain socket server beneran."""
    argv = [str(tree / "Dokumen"), str(tree / "Galeri"), "--code", "4815", "--no-qr"]
    cfg = L.parse_args(argv)
    L.ST.fails.clear()
    L.ST.sessions.clear()
    L.configure(cfg)
    return tree


def run_loop(lines):
    import lanshare.console as console_mod

    httpd = FakeServer()
    old_stdin = console_mod.sys.stdin
    console_mod.sys.stdin = FakeStdin(lines)
    try:
        console_loop(httpd)
    finally:
        console_mod.sys.stdin = old_stdin
    return httpd


def test_console_available_closed_stdin(monkeypatch):
    import lanshare.console as console_mod

    monkeypatch.setattr(console_mod.sys, "stdin", None)
    assert console_available() is False


def test_console_available_non_tty(monkeypatch):
    import lanshare.console as console_mod

    fake = FakeStdin([])
    monkeypatch.setattr(console_mod.sys, "stdin", fake)
    assert console_available() is True


def test_console_available_background_process(monkeypatch):
    import lanshare.console as console_mod

    class TtyStdin(FakeStdin):
        def isatty(self):
            return True

        def fileno(self):
            return 0

    monkeypatch.setattr(console_mod.sys, "stdin", TtyStdin([]))
    monkeypatch.setattr(console_mod.os, "getpgrp", lambda: 1)
    monkeypatch.setattr(console_mod.os, "tcgetpgrp", lambda fd: 2)
    assert console_available() is False


def test_quit_shuts_down_server(configured):
    httpd = run_loop(["q"])
    assert httpd.shutdown_called is True


def test_blank_lines_are_skipped(configured):
    # baris kosong beneran itu "\n" (Enter doang) - string kosong "" berarti EOF di readline().
    httpd = run_loop(["\n", "   \n", "q"])
    assert httpd.shutdown_called is True


def test_eof_returns_without_shutdown(configured):
    httpd = run_loop([])
    assert httpd.shutdown_called is False


def test_help_prints_command_list(configured, capsys):
    run_loop(["?", "q"])
    out = capsys.readouterr().out
    assert "ls" in out and "rm" in out


def test_ls_lists_current_mounts(configured, capsys):
    run_loop(["ls", "q"])
    out = capsys.readouterr().out
    assert "Dokumen" in out and "Galeri" in out


def test_add_path_via_drag_and_drop(configured, capsys, tree):
    new_dir = tree / "lain"
    run_loop([str(new_dir), "q"])
    out = capsys.readouterr().out
    assert "lain" in out
    assert "lain" in L.ST.mounts


def test_add_unknown_path_reports_error(configured, capsys):
    run_loop(["/path/yang/pasti/nggak/ada", "q"])
    out = capsys.readouterr().out
    assert "✗" in out
    assert "/path/yang/pasti/nggak/ada" not in L.ST.mounts.values()


def test_rm_by_name_and_by_index(configured, capsys):
    assert "Dokumen" in L.ST.mounts
    run_loop(["rm Dokumen", "q"])
    assert "Dokumen" not in L.ST.mounts
    remaining = next(iter(L.ST.mounts))
    run_loop(["rm 1", "q"])
    assert remaining not in L.ST.mounts


def test_rm_unknown_name_reports_error(configured, capsys):
    run_loop(["rm nggak-ada-beneran", "q"])
    out = capsys.readouterr().out
    assert "Nggak ketemu" in out


def test_qr_reprints_banner(configured, capsys):
    run_loop(["qr", "q"])
    out = capsys.readouterr().out
    assert L.ST.code in out


def test_unbalanced_quotes_treated_as_literal_path(configured, capsys):
    # shlex.split() gagal di kutip nggak seimbang - fallback ke baris apa adanya sbg 1 path.
    run_loop(['"nggak ada path kayak gini', "q"])
    out = capsys.readouterr().out
    assert "✗" in out


def test_loop_survives_unexpected_exception(configured, capsys, monkeypatch):
    import lanshare.console as console_mod

    def boom(_tokens):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(console_mod, "add_paths", boom)
    httpd = run_loop(["some/path", "q"])
    out = capsys.readouterr().out
    assert "kaboom" in out
    assert httpd.shutdown_called is True


def test_print_shared_empty(monkeypatch, capsys):
    monkeypatch.setattr(L.ST, "mounts", {})
    print_shared()
    assert "Belum ada" in capsys.readouterr().out


def test_console_help_mentions_all_commands():
    assert all(cmd in CONSOLE_HELP for cmd in ("ls", "rm", "qr", "q"))
