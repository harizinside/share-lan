import importlib
import subprocess
from types import SimpleNamespace

import pytest

from lanshare import cli

U = importlib.import_module("lanshare.update")


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(U, "distribution", lambda _: SimpleNamespace(read_text=lambda _: "{}"))
    monkeypatch.setattr(U.importlib.util, "find_spec", lambda _: object())


def test_update_dispatch(monkeypatch):
    monkeypatch.setattr(cli, "update", lambda: 7)
    assert cli.main(["update"]) == 7
    assert cli.parse_args(["./update"]).paths == ["./update"]


def test_update_rejects_arguments(monkeypatch):
    monkeypatch.setattr(cli, "update", lambda: pytest.fail("must not update"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["update", "unexpected"])
    assert exc.value.code == 2


def test_pip_targets_current_python(installed, monkeypatch):
    calls = []
    monkeypatch.setattr(
        U.subprocess,
        "run",
        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    assert U.update() == 0
    assert calls[0][:4] == [U.sys.executable, "-m", "pip", "install"]
    assert calls[0][-1] == U.SOURCE_URL


def test_uv_fallback(installed, monkeypatch):
    monkeypatch.setattr(U.importlib.util, "find_spec", lambda _: None)
    monkeypatch.setattr(U.shutil, "which", lambda _: "/bin/uv")
    calls = []
    monkeypatch.setattr(
        U.subprocess,
        "run",
        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    assert U.update() == 0
    assert calls[0][:5] == ["/bin/uv", "pip", "install", "--python", U.sys.executable]


def test_editable_untouched(monkeypatch):
    monkeypatch.setattr(
        U,
        "distribution",
        lambda _: SimpleNamespace(read_text=lambda _: '{"dir_info":{"editable":true}}'),
    )
    monkeypatch.setattr(U.subprocess, "run", lambda *a, **kw: pytest.fail("must not install"))
    assert U.update() == 1


def test_failure_propagates(installed, monkeypatch):
    monkeypatch.setattr(U.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 2))
    assert U.update() == 2
