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
    """Coba port yang diminta, loncat kalau kepake."""
    last = None
    for candidate in range(port, port + 21):
        try:
            return Server((host, candidate), Handler), candidate
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
            last = e
    die(f"Port {port}–{port + 20} kepake semua ({last}). Coba --port lain.")


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
        description="Bagi file ke satu jaringan. Seret file/folder ke terminal buat nempelin path-nya.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="contoh:\n"
        "  lanshare                             # nyala kosong, path dilempar belakangan\n"
        "  lanshare ~/Desktop/video.mp4\n"
        "  lanshare ~/Documents ~/Foto\\ Liburan/ laporan.pdf\n"
        "  lanshare . --read-only --code 1234\n"
        "  sharelan update                      # update instalasi dari GitHub main\n"
        "  sharelan ./update                    # bagikan folder bernama update\n"
        "\n"
        "Sambil server jalan, seret file/folder ke terminal + Enter buat nambahin.\n"
        "Ketik ? di situ buat lihat perintah lain (ls, rm, qr, q).\n",
    )
    ap.add_argument("--version", action="version", version=f"sharelan {installed_version()}")
    ap.add_argument(
        "paths",
        nargs="*",
        default=[],
        help="file/folder yang mau dibagikan (wajib disebutin; pakai '.' buat folder sekarang)",
    )
    ap.add_argument(
        "--port", type=int, default=8000, help="port (default 8000, loncat kalau kepake)"
    )
    ap.add_argument("--host", default="0.0.0.0", help="alamat bind (default 0.0.0.0)")
    ap.add_argument("--ip", help="paksa alamat yang ditampilin di QR")
    ap.add_argument("--code", help="kode akses sendiri (default: acak)")
    ap.add_argument("--code-len", type=int, default=4, help="panjang kode acak (default 4)")
    ap.add_argument(
        "--max-upload", type=parse_size, default=0, help="batas ukuran per upload, mis. 2G"
    )
    ap.add_argument("--read-only", action="store_true", help="matiin upload")
    ap.add_argument("--hidden", action="store_true", help="ikutin file/folder tersembunyi")
    ap.add_argument("--no-qr", action="store_true", help="jangan cetak QR di terminal")
    ap.add_argument(
        "--zip-resume-limit",
        type=parse_size,
        default=20 << 30,
        help="ZIP di bawah ukuran ini dibikin bisa di-resume (default 20G)",
    )
    ap.add_argument("--no-caffeinate", action="store_true", help="jangan cegah Mac ketiduran")
    return ap.parse_args(argv)


def configure(cfg):
    """Isi ST dari argumen CLI. Dipisah dari main() biar bisa dites."""
    ST.cfg = cfg
    if cfg.code_len < 3 or cfg.code_len > 12:
        die("--code-len harus antara 3 dan 12.")
    ST.code = str(cfg.code) if cfg.code else new_code(cfg.code_len)
    if not ST.code.strip():
        die("--code nggak boleh kosong.")

    ST.mounts = build_mounts(cfg.paths)
    recompute()
    ST.addresses = find_addresses()
    return ST


def main(argv=None):
    try:
        sys.stdout.reconfigure(line_buffering=True)  # banner langsung nongol walau di-pipe
    except Exception:
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["update"]:
        parser = argparse.ArgumentParser(
            prog="sharelan update", description="Update aplikasi dari GitHub main."
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
        print(f"  {C.dim('Mode baca-saja: upload dimatiin.')}\n")
    elif not any(os.path.isdir(p) and os.access(p, os.W_OK) for p in ST.mounts.values()):
        print(f"  {C.dim('Nggak ada folder yang bisa ditulis, jadi upload dimatiin.')}\n")

    if console_available():
        print(f"  {C.dim('Seret file ke sini + Enter buat nambahin. Ketik ? buat bantuan.')}\n")
        threading.Thread(target=console_loop, args=(httpd,), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n  {C.ok('✓')} Server dimatiin. Akses langsung putus.\n")
    finally:
        httpd.server_close()
        if awake:
            awake.terminate()
    return 0
