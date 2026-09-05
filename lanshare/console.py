import os
import shlex
import sys

from .banner import print_banner
from .fmt import C, human
from .mounts import add_paths, dir_stats, remove_mount
from .state import ST

CONSOLE_HELP = """
  Seret file/folder dari Finder ke jendela ini terus Enter buat nambahin.
  Atau ketik path-nya langsung. Bisa beberapa sekaligus.

    ls          lihat yang lagi dibagikan
    rm <nama>   cabut satu (boleh pakai nomor urutnya, mis. rm 2)
    qr          tampilkan ulang alamat + QR
    q           matiin server
"""


def print_shared():
    if not ST.mounts:
        print(f"\n  {C.dim('Belum ada yang dibagikan.')}\n")
        return
    print()
    for i, (name, full) in enumerate(ST.mounts.items(), 1):
        count, size, cut = dir_stats(full, budget=0.4)
        if os.path.isdir(full):
            info = f"{count}{'+' if cut else ''} file, {human(size)}{'+' if cut else ''}"
        else:
            info = human(size)
        print(f"  {C.dim(str(i) + '.')} {name}  {C.dim('(' + info + ')')}")
    print()


def console_available():
    """Aman baca stdin? Proses yang lagi di background nggak boleh baca dari terminal -
    bisa kena SIGTTIN dan malah ngestop sendiri. Pipe/file aman-aman aja."""
    if not sys.stdin or sys.stdin.closed:
        return False
    try:
        if sys.stdin.isatty():
            return os.getpgrp() == os.tcgetpgrp(sys.stdin.fileno())
    except OSError:
        return False
    return True


def console_loop(httpd):
    """Baca perintah dari stdin sambil server jalan."""
    while True:
        # readline(), bukan `for x in sys.stdin` - iterasi file object nge-buffer
        # dan barisnya bisa nyangkut sampai buffer penuh.
        raw = sys.stdin.readline()
        if not raw:
            return  # stdin ketutup: server tetap jalan, cuma nggak bisa diperintah
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        try:
            if low in ("q", "quit", "exit"):
                httpd.shutdown()
                return
            if low in ("?", "h", "help"):
                print(CONSOLE_HELP)
            elif low == "ls":
                print_shared()
            elif low == "qr":
                print_banner(reprint=True)
            elif low.startswith("rm "):
                name = remove_mount(line[3:].strip())
                if name:
                    print(f"  {C.warn('−')} {name} {C.dim('dicabut')}")
                    print_shared()
                else:
                    print(f"  {C.bad('✗')} Nggak ketemu: {line[3:].strip()}")
            else:
                try:
                    tokens = shlex.split(line)
                except ValueError:
                    tokens = [line]  # kutip nggak seimbang: anggap satu path apa adanya
                added, skipped, bad = add_paths(tokens)
                for name, full in added:
                    count, size, _ = dir_stats(full, budget=0.4)
                    info = f"{count} file, {human(size)}" if os.path.isdir(full) else human(size)
                    print(f"  {C.ok('+')} {name}  {C.dim('(' + info + ')')}")
                for raw_path in skipped:
                    print(f"  {C.dim('·')} {C.dim(raw_path + ' udah dibagikan')}")
                for raw_path, why in bad:
                    print(f"  {C.bad('✗')} {raw_path}  {C.dim('(' + why + ')')}")
                if added:
                    total = len(ST.mounts)
                    print(
                        f"  {C.dim(f'Sekarang {total} item dibagikan. Yang lagi buka halamannya')}"
                    )
                    print(f"  {C.dim('bakal lihat sendiri dalam beberapa detik.')}")
        except Exception as e:  # konsol nggak boleh sampai matiin server
            print(f"  {C.bad('✗')} {e}")
