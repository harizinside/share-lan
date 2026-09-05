# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`share·lan` is a Python LAN file-sharing server, packaged as `lanshare/`. You point it at
files/folders, it serves them over HTTP to anyone on the same WiFi/LAN via a 4-digit access code
or QR scan — no cloud, no accounts, no client install. README.md (in Indonesian) is the
user-facing doc; treat it as authoritative for behavior/UX decisions.

## Commands

```bash
uv run --group dev ruff check .      # lint
uv run --group dev ruff format .     # format
uv run --group dev pytest            # run all tests (109 tests, ~3s)
uv run --group dev pytest tests/test_http.py::test_name   # run a single test
uv run --group dev pytest tests/test_zip.py                # run one file's tests

uv run lanshare ~/some/file.txt      # run the server (console-script from pyproject.toml)
uv tool install --editable . && lanshare ...   # install once, then call `lanshare` from anywhere
```

Runtime deps (`qrcode`, `pillow`, `pypdfium2`) and dev deps (`pytest`, `ruff`) both live in
`pyproject.toml` (`[project.dependencies]` and the `dev` dependency-group respectively) — there's
no separate PEP 723 header to keep in sync anymore. Pillow (thumbnails) and pypdfium2 (PDF
thumbnails) are guarded at import time by `HAVE_PIL`/`HAVE_PDF` in `lanshare/thumb.py` and no-op
without them. `groupdocs-viewer-net` (Office-document `/preview` rendering) is a true optional
extra — `[project.optional-dependencies] viewer` — installed via `uv run --extra viewer`; guarded
by `HAVE_GROUPDOCS` in `lanshare/preview.py`.

## Architecture

The app is a package, `lanshare/`, split into one module per responsibility. The import graph is
a strict DAG (no cycles) — each row only imports from rows above it:

| Module | Responsibility |
|---|---|
| `state.py` | Top-level constants and the single `State`/`ST` singleton (all cross-thread state) |
| `fmt.py` | Size formatting, ANSI color helper `C`, `log`/`die` |
| `qr.py` | QR matrix/SVG/ASCII rendering |
| `network.py` | Picks which local IP to advertise (scores interfaces, prefers the default route) |
| `mounts.py` | Path <-> display-name mapping, path-traversal guard (`resolve`, `Denied`, `Missing`), directory listing/stats |
| `thumb.py` | `HAVE_PIL` + checksums (`crc32_of`, `sha256_of`) + in-memory thumbnail cache |
| `banner.py` | The terminal banner printed on startup / `qr` console command |
| `ziputil.py` | The resumable-ZIP engine (byte-exact plan + emit) |
| `preview.py` | `/preview` document rendering — GroupDocs.Viewer for Office formats when installed (optional `viewer` extra), guarded by `HAVE_GROUPDOCS` |
| `pages.py` | All served HTML/CSS/JS as inline string templates |
| `auth.py` | 4-digit code auth, session cookies, brute-force lockout |
| `console.py` | The interactive `ls`/`rm`/`qr`/`q` REPL on stdin |
| `httpserver.py` | `Handler`/`Server` — the actual HTTP routing |
| `cli.py` | argparse, `configure()`, `main()` |
| `update.py` | `sharelan update` — reinstalls from the latest tagged GitHub release via pip/uv |

`lanshare/__init__.py` re-exports the public surface of all of the above (with `__all__`, so ruff
doesn't flag the re-exports as unused) — this is what `tests/conftest.py`'s `import lanshare as L`
relies on, and what keeps `L.ST`, `L.Handler`, etc. working exactly as before the split.

**One deliberate wrinkle:** `tests/conftest.py` monkeypatches `L.fmt.log = lambda *a, **k: None` to
silence server logging during tests. All `log(...)` call sites live in `httpserver.py`, and that
module calls it as `fmt.log(...)` (via `from . import fmt`) rather than `from .fmt import log` —
importing the bare name would bind a snapshot reference that the monkeypatch couldn't reach.
Follow the same qualified-import pattern (`from . import fmt`, then `fmt.something(...)`) for any
new cross-module call that tests might need to patch.

- **`State` (`ST`)**, in `state.py` — single global object holding all cross-thread state: active
  mounts, auth sessions/lockouts, CRC/SHA256/thumbnail/ZIP-plan caches, upload dir, revision
  counter. Guarded by `ST.lock` where mutated concurrently. There is exactly one instance, shared
  by every module via `from .state import ST`.
- **Mounts** (`mounts.py`: `build_mounts`, `add_paths`, `remove_mount`) — the mapping from a
  dropped filesystem path to the display name the recipient sees. Adding/removing paths while the
  server is live goes through this and bumps `ST.revision` so connected clients' `/api/rev` poll
  picks up the change.
- **Path resolution** (`mounts.py`: `resolve`, `Denied`, `Missing`) — every incoming `p` query
  param is resolved back against the mounts and must not escape them; this is the security
  boundary for path traversal and outward-pointing symlinks. Always route new file-serving code
  through `resolve()` rather than joining paths manually.
- **Auth** (`auth.py`: `check_code`, `lock_left`, session cookies) — 4-digit code auth with per-IP
  exponential-backoff lockout (`ST.fails`) plus a global failure counter (`ST.global_fails`) that
  auto-rotates the code after too many attempts LAN-wide.
- **ZIP engine** (`ziputil.py`: `build_zip_plan`, `emit_plan`, `StreamWriter`) — the resumable-ZIP
  trick: for folders under `--zip-resume-limit` (default 20G), CRCs are precomputed and the exact
  ZIP byte layout is planned up front so a `Range` request into the middle of the ZIP reproduces
  byte-for-byte what a full download would have produced at that offset (this is what
  `tests/test_zip.py` verifies). Above the limit, it falls back to a streaming (non-resumable) ZIP
  and the UI offers a copyable URL list instead.
- **HTTP layer** (`httpserver.py`: `Handler`, `Server`) — a `BaseHTTPRequestHandler` subclass with
  hand-rolled routing in `do_GET`/`do_POST`/`do_PUT` (no framework). Routes split into page routes
  (`/`, `/login`), JSON `/api/*` routes (list, rev, zipinfo, hash), and byte-serving routes (`/dl`,
  `/zip`, `/thumb`, `/urls`, `/sums`, `/qr`). `pump()` uses `socket.sendfile` (zero-copy) with a
  manual-copy fallback for platforms that don't support it. `/dl` supports `Range`/multi-range for
  resumable downloads.
- **Frontend** (`pages.py`: `page_html`, `login_page`) — the entire recipient-facing UI
  (HTML/CSS/JS) is generated as inline strings served from these functions; there's no separate
  static asset pipeline or build step.
- **Networking** (`network.py`: `find_addresses`, `_route_ip`, `_score`) — picks which local IP to
  advertise in the QR/banner among multiple interfaces (VPN/Docker/Tailscale can all present
  addresses), scoring candidates and preferring the one that matches the default route.
- **Console loop** (`console.py`: `console_loop`) — the interactive `ls`/`rm`/`qr`/`q` REPL that
  runs on stdin while the server thread serves requests, letting users add/remove shared paths
  without restarting.

## Test layout

Tests spin up a real server on a random port per test (`tests/conftest.py:start`) rather than
mocking the HTTP layer — prefer that pattern for new HTTP-facing tests.

- `tests/test_unit.py` — pure functions: size formatting, `--max-upload` parsing, upload name
  sanitization, `Range` header parsing, duplicate mount naming.
- `tests/test_paths.py` — path security: `../`, absolute paths, outward symlinks, dotfiles,
  in-progress `.part` files.
- `tests/test_zip.py` — exact ZIP byte layout and that a resumed (ranged) fetch matches a
  from-scratch fetch, ZIP64, unicode names, CRC caching.
- `tests/test_http.py` — end-to-end over real HTTP: auth, QR-scan flow, rate limiting,
  Range/416/multi-range, ZIP resume, streaming-ZIP mode, uploads, size limits, read-only mode,
  `/sums`, `/urls`, `/qr`, `/thumb`.
