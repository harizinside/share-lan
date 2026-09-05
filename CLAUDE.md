# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`share·lan` (`lanshare.py`) is a single-file Python LAN file-sharing server. You point it at
files/folders, it serves them over HTTP to anyone on the same WiFi/LAN via a 4-digit access code
or QR scan — no cloud, no accounts, no client install. Everything (server, HTTP handling, HTML/JS
frontend, ZIP streaming, QR generation) lives in `lanshare.py` (~2400 lines). README.md (in
Indonesian) is the user-facing doc; treat it as authoritative for behavior/UX decisions.

## Commands

```bash
uv run --group dev ruff check .      # lint
uv run --group dev ruff format .     # format
uv run --group dev pytest            # run all tests (100 tests, ~3s)
uv run --group dev pytest tests/test_http.py::test_name   # run a single test
uv run --group dev pytest tests/test_zip.py                # run one file's tests

./lanshare.py ~/some/file.txt        # run the server directly (executable, PEP 723 shebang)
```

`uv` manages the dependency groups (`pyproject.toml`): the app itself only needs `qrcode` +
`pillow` (declared in the PEP 723 header at the top of `lanshare.py`, not in `pyproject.toml`),
while `dev` adds `pytest`/`ruff`. This means `./lanshare.py` runs standalone via `uv run --script`
without touching the dev group — don't add runtime deps to `pyproject.toml`; add them to the PEP
723 header block instead.

Pillow is optional at runtime: guarded by `HAVE_PIL`, thumbnails just no-op without it.

## Architecture

Everything runs in one process, one file, organized into clearly marked `# ----` sections (search
for these to navigate — there is no module split to worry about):

- **`State` (`ST`)** — single global object holding all cross-thread state: active mounts, auth
  sessions/lockouts, CRC/SHA256/ZIP-plan caches, upload dir, revision counter. Guarded by
  `ST.lock` where mutated concurrently. There is exactly one instance; tests reset relevant fields
  between runs (see `tests/conftest.py`).
- **Mounts (`build_mounts`, `add_paths`, `remove_mount`)** — the mapping from a dropped
  filesystem path to the display name the recipient sees. Adding/removing paths while the server
  is live goes through this and bumps `ST.revision` so connected clients' `/api/rev` poll picks up
  the change.
- **Path resolution (`resolve`, `Denied`, `Missing`)** — every incoming `p` query param is
  resolved back against the mounts and must not escape them; this is the security boundary for
  path traversal and outward-pointing symlinks. Always route new file-serving code through
  `resolve()` rather than joining paths manually.
- **Auth (`check_code`, `lock_left`, session cookies)** — 4-digit code auth with per-IP
  exponential-backoff lockout (`ST.fails`) plus a global failure counter (`ST.global_fails`) that
  auto-rotates the code after too many attempts LAN-wide. Sessions are cookie tokens in
  `ST.sessions` with a TTL.
- **ZIP engine (`build_zip_plan`, `emit_plan`, `StreamWriter`, `_central_record`/`_end_record`)**
  — the resumable-ZIP trick: for folders under `--zip-resume-limit` (default 20G), CRCs are
  precomputed and the exact ZIP byte layout is planned up front so a `Range` request into the
  middle of the ZIP reproduces byte-for-byte what a full download would have produced at that
  offset (this is what `tests/test_zip.py` verifies). Above the limit, it falls back to a
  streaming (non-resumable) ZIP and the UI offers a copyable URL list instead.
- **HTTP layer (`Handler`, `Server`)** — a `BaseHTTPRequestHandler` subclass with hand-rolled
  routing in `do_GET`/`do_POST`/`do_PUT` (no framework). Routes split into page routes (`/`,
  `/login`), JSON `/api/*` routes (list, rev, zipinfo, hash), and byte-serving routes (`/dl`,
  `/zip`, `/thumb`, `/urls`, `/sums`, `/qr`). `pump()` uses `socket.sendfile` (zero-copy) with a
  manual-copy fallback for platforms that don't support it. `/dl` supports `Range`/multi-range for
  resumable downloads.
- **Frontend (`page_html`, `login_page`)** — the entire recipient-facing UI (HTML/CSS/JS) is
  generated as inline strings served from these functions; there's no separate static asset
  pipeline or build step.
- **Networking (`find_addresses`, `_route_ip`, `_score`)** — picks which local IP to advertise in
  the QR/banner among multiple interfaces (VPN/Docker/Tailscale can all present addresses),
  scoring candidates and preferring the one that matches the default route.
- **Console loop (`console_loop`)** — the interactive `ls`/`rm`/`qr`/`q` REPL that runs on stdin
  while the server thread serves requests, letting users add/remove shared paths without
  restarting.

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
