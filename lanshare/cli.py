import argparse
import errno
import os
import subprocess
import sys
import threading

from .auth import new_code
from .banner import print_banner
from .console import console_available, console_loop
from .fmt import C, die, parse_size
from .httpserver import Handler, Server
from .mounts import build_mounts, recompute
from .network import find_addresses
from .state import ST
from .update import installed_version, update


def bind_server(host, port):
    """Try the requested port, hop to the next one if it's taken."""
    last = None
    for candidate in range(port, port + 21):
        try:
            return Server((host, candidate), Handler), candidate
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
            last = e
    die(f"Ports {port}-{port + 20} are all in use ({last}). Try a different --port.")


def keep_awake():
    if sys.platform != "darwin" or ST.cfg.no_caffeinate:
        return None
    try:
        return subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        prog="lanshare",
        description="Share files over a single network. Drag a file/folder into the terminal to paste its path.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
        "  lanshare                             # start empty, add paths later\n"
        "  lanshare ~/Desktop/video.mp4\n"
        "  lanshare ~/Documents ~/Vacation\\ Photos/ report.pdf\n"
        "  lanshare . --read-only --code 1234\n"
        "  sharelan update                      # update this install to the latest GitHub release\n"
        "  sharelan ./update                    # share a folder literally named 'update'\n"
        "\n"
        "While the server is running, drag a file/folder into the terminal + Enter to add it.\n"
        "Type ? there to see the other commands (ls, rm, qr, q).\n",
    )
    ap.add_argument("--version", action="version", version=f"sharelan {installed_version()}")
    ap.add_argument(
        "paths",
        nargs="*",
        default=[],
        help="files/folders to share (optional; use '.' for the current folder)",
    )
    ap.add_argument(
        "--port", type=int, default=8000, help="port (default 8000, hops to the next free one)"
    )
    ap.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    ap.add_argument("--ip", help="force the address shown in the QR code")
    ap.add_argument("--code", help="use this access code instead of a random one")
    ap.add_argument("--code-len", type=int, default=4, help="random code length (default 4)")
    ap.add_argument(
        "--max-upload", type=parse_size, default=0, help="per-upload size limit, e.g. 2G"
    )
    ap.add_argument("--read-only", action="store_true", help="disable uploads")
    ap.add_argument("--hidden", action="store_true", help="include hidden files/folders")
    ap.add_argument("--no-qr", action="store_true", help="don't print the QR code in the terminal")
    ap.add_argument(
        "--zip-resume-limit",
        type=parse_size,
        default=20 << 30,
        help="folders under this size get a resumable ZIP (default 20G)",
    )
    ap.add_argument(
        "--no-caffeinate", action="store_true", help="don't prevent the Mac from sleeping"
    )
    return ap.parse_args(argv)


def configure(cfg):
    """Fill ST from CLI arguments. Split out from main() so it's testable."""
    ST.cfg = cfg
    if cfg.code_len < 3 or cfg.code_len > 12:
        die("--code-len must be between 3 and 12.")
    ST.code = str(cfg.code) if cfg.code else new_code(cfg.code_len)
    if not ST.code.strip():
        die("--code cannot be empty.")

    ST.mounts = build_mounts(cfg.paths)
    recompute()
    ST.addresses = find_addresses()
    return ST


def main(argv=None):
    try:
        sys.stdout.reconfigure(line_buffering=True)  # banner shows up right away even when piped
    except Exception:
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["update"]:
        parser = argparse.ArgumentParser(
            prog="sharelan update", description="Update the app to the latest GitHub release."
        )
        parser.parse_args(argv[1:])
        return update()
    cfg = parse_args(argv)
    configure(cfg)

    if cfg.ip:
        ip = cfg.ip
    elif ST.addresses:
        ip = ST.addresses[0][0]
    else:
        ip = "127.0.0.1"

    httpd, port = bind_server(cfg.host, cfg.port)
    cfg.port = port
    ST.base_url = f"http://{ip}:{port}"

    awake = keep_awake()
    print_banner()
    if cfg.read_only:
        print(f"  {C.dim('Read-only mode: uploads disabled.')}\n")
    elif not any(os.path.isdir(p) and os.access(p, os.W_OK) for p in ST.mounts.values()):
        print(f"  {C.dim('No writable folder available, so uploads are disabled.')}\n")

    if console_available():
        print(f"  {C.dim('Drag a file here + Enter to add it. Type ? for help.')}\n")
        threading.Thread(target=console_loop, args=(httpd,), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n  {C.ok('✓')} Server stopped. Access is cut off immediately.\n")
    finally:
        httpd.server_close()
        if awake:
            awake.terminate()
    return 0
