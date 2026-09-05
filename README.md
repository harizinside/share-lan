# share·lan

[![Release](https://img.shields.io/github/v/release/harizinside/share-lan)](https://github.com/harizinside/share-lan/releases/latest)
[![Docker Publish](https://github.com/harizinside/share-lan/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/harizinside/share-lan/actions/workflows/docker-publish.yml)
[![GHCR](https://img.shields.io/badge/ghcr.io-share--lan-2496ED?logo=docker&logoColor=white)](https://github.com/harizinside/share-lan/pkgs/container/share-lan)

Share files over a single WiFi/LAN network. Recipients just **scan a QR code** (phone) or **type
the IP + a 4-digit code** (laptop). No cloud, no accounts, and recipients don't need to install
anything — just a browser.

```bash
uv run lanshare ~/Desktop/video.mp4
```

![share·lan file list](docs/01-daftar.png)

**With no arguments, the server starts empty** — zero files exposed. You add paths later while
the server keeps running (see the next section). This is deliberate: defaulting to the current
folder is a trap that could expose your source code or home folder without you noticing. Want to
share the current folder anyway? Say so explicitly: `uv run lanshare .`

## How to use it: the terminal as a drop zone

Type `uv run lanshare ` (with a trailing space), then **drag files/folders from Finder into the
terminal window** — the path pastes itself in, spaces already escaped. Drop as many as you like,
mixing files and folders is fine, then hit Enter.

```bash
uv run lanshare ~/Desktop/video.mp4 ~/Documents/report.pdf ~/Holiday\ Photos/
```

What shows up in the terminal:

```
  ◆ share·lan  — running. Ctrl-C to stop.

  Shared: 3 items
    • video.mp4        (412 MB)
    • report.pdf       (2.1 MB)
    • Holiday Photos/  (128 files, 3.4 GB)

  Address: http://192.168.1.7:8000   ← used in the QR
  Also   : http://macbook.local:8000  (survives an IP change)
  Code   : 4815

  [ QR code ]

  → To a phone : scan the QR above
  → To a laptop: give them the address + code above
```

**To a phone**: point the built-in camera at the QR — the access code rides along in the URL, so
zero typing is needed.
**To a laptop**: tell them the address + the 4 digits, they type it into a browser.

### Adding files while the server is running

The server can start empty and get filled in later — the QR is scannable from the very start:

```bash
uv run lanshare
```

Once it's running, the terminal becomes the control surface. Drag a file/folder from Finder into
the terminal window and hit Enter, or type the path directly:

```
  + video.mp4  (412 MB)
  Now sharing 1 item.

ls          show what's currently shared
rm <name>   remove one (you can use its list index, e.g. rm 2)
qr          reprint the address + QR
q           stop the server
```

A page that's **already open on someone's phone updates itself** within a few seconds — no need
to ask them to refresh, and a QR they already scanned stays valid. If you `rm` something, access
to it is cut immediately.

### No file is ever copied

The only thing the server stores is the **path**. A 200 GB file in `~/Documents` stays right there
in `~/Documents` — it's read straight from that location whenever someone downloads it, never
moved, copied, or placed in the folder you launched the server from. The only thing that actually
gets written to disk is **whatever someone else uploads to you**, and that lands in whichever
folder **the recipient has open when they upload** — never dropped at the root.

If what you dropped was a **single file**, the QR points straight at that file — scan it and the
download starts immediately, no listing page in between.

## What recipients can do

- Browse folders, search for files, list or grid view with thumbnails
- Download a single file, or a whole folder at once as a ZIP
- **Preview right in the browser** — click the eye icon: PDF, images, video, audio, and text
  open in the browser's built-in viewer; Office formats (docx, xlsx, pptx, odt, rtf, etc.) render
  to HTML via [GroupDocs.Viewer](https://pypi.org/project/groupdocs-viewer-net/) when it's
  installed (`uv pip install groupdocs-viewer-net` or `uv run --extra viewer`)
- Click the QR button on any row → a large QR just for that item (great for pointing one file at
  someone's phone)
- Stream video/audio right in the browser without downloading the whole file
- Send files back to you (drag & drop, can be disabled with `--read-only`)
- Grab the SHA-256 of a single file, or a folder's `SHA256SUMS`, to verify the transfer

### What it looks like

A folder that's mostly images opens as a gallery, complete with thumbnails — not a plain text
list:

![Grid view with thumbnails](docs/02-galeri.png)

Every row has its own QR button. Click it → a large QR appears in the middle of the screen, hand
the phone over, and their download starts instantly:

![Per-file QR overlay](docs/03-qr.png)

The theme follows the system, and light mode is a real design pass — not just inverted colors:

![Light theme](docs/04-terang.png)

On a phone, the listing reflows into one or two columns with larger touch targets:

<p align="center"><img src="docs/05-hp.png" width="330" alt="Mobile view"></p>

## Large file transfers

**Single-file downloads always resume.** It uses `Accept-Ranges` + `sendfile` (zero-copy in the
kernel), so a connection that drops at 180 GB just continues instead of starting over.

**Folder ZIPs** have two modes, chosen automatically based on size:

| Folder size | Mode | Resumable? |
|---|---|---|
| ≤ 20 GB (`--zip-resume-limit`) | CRCs precomputed, ZIP layout planned exactly | **Yes** |
| > 20 GB | Streaming | No |

For anything above the threshold, the UI is upfront about it and offers a **"Copy URL list"**
button. Paste the result into a file, then on the recipient's end:

```bash
aria2c -i list.txt          # parallel + per-file resume
wget -c -i list.txt         # alternative
```

For hundreds of GB, this is the sanest approach: parallel downloads, and if one file fails, only
that one needs retrying.

## Options

```
lanshare [PATH ...]

  (no PATH: server starts empty, paths are added later via the terminal)

  --port N              port (default 8000, hops to the next free one if taken)
  --host ADDR           bind address (default 0.0.0.0)
  --ip ADDR             force the address shown in the QR
  --code CODE           custom access code (default: random)
  --code-len N          length of the random code (default 4)
  --max-upload SIZE     limit per upload, e.g. 2G
  --read-only           disable uploads
  --hidden              include hidden files/folders
  --no-qr               don't print the QR in the terminal
  --zip-resume-limit N  threshold for a resumable ZIP (default 20G)
  --no-caffeinate       don't prevent the Mac from sleeping
```

## Security

- **Plain HTTP, unencrypted.** Fine for a home/office WiFi network; don't use it for sensitive
  data on public WiFi (cafes, airports).
- **The 4-digit code is protected by rate-limiting, not by length.** 5 wrong guesses locks that IP
  for 60 seconds, and each further failed round doubles the lockout (up to 15 minutes). Since
  changing IP on a LAN is trivial, there's also a global counter: 50 failures anywhere → the code
  is rotated automatically and a new QR is printed. Need it stricter? `--code-len 6`.
- Every path is validated back against the folders you shared — `../` and symlinks pointing
  outside are rejected.
- Uploaded filenames are sanitized (path components, `..`, control characters, Windows reserved
  names), plus a disk-space check.
- **The access code is not a substitute for a VPN** — it only stops casual snooping from someone
  scanning ports on the same network.
- The server **never moves or copies anything**. Files stay exactly where they are and are only
  read when someone downloads them. Ctrl-C = access is cut immediately.

## If it won't open on a phone

1. Make sure both devices are on the same WiFi network.
2. macOS will ask for firewall permission the first time it runs → choose **Allow**.
3. Got a VPN/Docker/Tailscale running? The address picked might belong to that interface. The
   banner shows alternatives — try another one, or force it with `--ip`.
4. Office/hotel routers sometimes enable "AP isolation," which blocks devices from talking to each
   other.

## Technical notes

The server is a plain Python package (folder `lanshare/`, one small module per responsibility —
mounts, auth, HTTP, ZIP, pages, etc.), with dependencies (`qrcode`, `pillow`, `pypdfium2`)
declared in `pyproject.toml` — `uv` handles installing them. Pillow is optional: without it,
thumbnails are disabled and the UI falls back to icons, everything else works normally.
`groupdocs-viewer-net` is also optional (`--extra viewer`): without it, Office-document preview is
disabled and PDF/image/media still preview via the browser's built-in viewer. Everything else is
stdlib.

## Install with one line (no clone needed)

Don't have this repo on your machine yet? This one-liner installs `lanshare` directly, needing
only Python (no `uv`, `git`, or anything else required):

macOS / Ubuntu / Fedora / other Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/harizinside/share-lan/main/install.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/harizinside/share-lan/main/install.ps1 | iex"
```

After that, just call `sharelan` (or the old alias `lanshare`) from any terminal.

### Updating the app

Starting with v0.0.2, update an existing install to the latest GitHub release:

```bash
sharelan update
sharelan --version
```

`lanshare update` works too. If you're still on v0.0.1, re-run the one-line installer above once
to get this new command.
The update uses whichever Python installation is currently running; it needs an internet
connection. Once it's done, restart the server so the new code takes effect.
Editable install from the repo: use `git pull` then `uv sync`.
Docker: use `docker compose pull` then `docker compose up -d`.
To share a file/folder literally named `update`, write `sharelan ./update`.

## Running without typing `uv run`

`pyproject.toml` registers `lanshare` as a global command. Install it once with `uv tool install`,
and it can then be called directly from anywhere without `uv run`:

```bash
uv tool install --editable .
lanshare ~/Desktop/video.mp4
```

`--editable` means code changes take effect immediately, with no reinstall needed.

## Running via Docker

The official image is published to GHCR via GitHub Actions on every push to `main`/a release tag
(`.github/workflows/docker-publish.yml`) — the `docker-compose.yaml` in the repo root is ready to
use as-is:

```bash
mkdir -p shared && cp file-to-share shared/
docker compose up -d
docker compose logs -f      # see the QR + 4-digit code here
```

The host folder `./shared` is mounted to `/data` inside the container; edit `command:` in the
compose file to add other flags (`--read-only`, `--code`, `--max-upload`, etc. — see
[Options](#options)).

Networking note: only port 8000 is published by default, so share-lan runs fine, but the address
it auto-detects for the QR/banner may be Docker's internal address rather than the host's real LAN
IP. If that happens, uncomment `network_mode: host` below (Linux only) so share-lan sees the host
network directly, or force it manually with `--ip <host-lan-ip>`.

Want to build from source instead of pulling from GHCR? Change `image:` to `build: .` in the
compose file, or directly:

```bash
docker build -t share-lan .
docker run --rm -p 8000:8000 -v "$(pwd)/shared:/data" share-lan
```

## Contributing a translation

The web UI's language toggle reads from `lanshare/locales/` — one flat JSON file per language
(`en.json`, `id.json`, ...). To add a language: copy `en.json`, translate the values, save it as
`<code>.json` in that folder, and it shows up in the toggle automatically — no code changes
needed. See `lanshare/locales/README.md` for details; a partial translation is fine, since any key
you skip just falls back to English.

## Development

Linting, formatting, and tests use the Astral tools (`ruff` + `pytest`, managed by `uv`):

```bash
uv run --group dev ruff check .      # lint
uv run --group dev ruff format .     # format
uv run --group dev pytest            # tests
```

109 tests, running in ~3 seconds, split across four files:

| File | Covers |
|---|---|
| `tests/test_unit.py` | Size formatting, `--max-upload` parsing, upload-name sanitization, `Range` header parsing, duplicate mount naming |
| `tests/test_paths.py` | Path security: `../`, absolute paths, symlinks pointing outside, dotfiles, `.part` files |
| `tests/test_zip.py` | Exact ZIP byte layout, chunked fetches must be identical to a from-scratch fetch (**this is what guarantees resume**), ZIP64, unicode, CRC caching |
| `tests/test_http.py` | End-to-end over HTTP: auth, scan flow, rate limiting, Range/416/multi-range, ZIP resume, streaming mode, uploads, size limits, read-only, `/sums`, `/urls`, `/qr`, `/thumb` |

Configuration lives in `pyproject.toml`.
