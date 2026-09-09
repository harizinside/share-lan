import os
import shlex
import sys

from .banner import print_banner
from .fmt import C, human
from .mounts import add_paths, dir_stats, remove_mount
from .state import ST

CONSOLE_HELP = """
  Drag a file/folder from Finder into this window then Enter to add it.
  Or type the path directly. Multiple at once works too.

    ls          show what's currently shared
    rm <name>   remove one (you can use its list index, e.g. rm 2)
    qr          reprint the address + QR
    q           stop the server
"""


def print_shared():
    if not ST.mounts:
        print(f"\n  {C.dim('Nothing shared yet.')}\n")
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
    """Safe to read stdin? A process running in the background must not read from the
    terminal - it can get SIGTTIN and stop itself. Pipes/files are always fine."""
    if not sys.stdin or sys.stdin.closed:
        return False
    try:
        if sys.stdin.isatty() and hasattr(os, "getpgrp") and hasattr(os, "tcgetpgrp"):
            return os.getpgrp() == os.tcgetpgrp(sys.stdin.fileno())
    except OSError:
        return False
    return True


def console_loop(httpd):
    """Read commands from stdin while the server is running."""
    while True:
        # readline(), not `for x in sys.stdin` - iterating a file object buffers
        # and lines can be stuck waiting until the buffer fills up.
        raw = sys.stdin.readline()
        if not raw:
            return  # stdin closed: the server keeps running, just can't take commands
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
                    print(f"  {C.warn('−')} {name} {C.dim('removed')}")
                    print_shared()
                else:
                    print(f"  {C.bad('✗')} Not found: {line[3:].strip()}")
            else:
                try:
                    tokens = shlex.split(line)
                except ValueError:
                    tokens = [line]  # unbalanced quotes: treat the whole line as one path
                added, skipped, bad = add_paths(tokens)
                for name, full in added:
                    count, size, _ = dir_stats(full, budget=0.4)
                    info = f"{count} file, {human(size)}" if os.path.isdir(full) else human(size)
                    print(f"  {C.ok('+')} {name}  {C.dim('(' + info + ')')}")
                for raw_path in skipped:
                    print(f"  {C.dim('·')} {C.dim(raw_path + ' already shared')}")
                for raw_path, why in bad:
                    print(f"  {C.bad('✗')} {raw_path}  {C.dim('(' + why + ')')}")
                if added:
                    total = len(ST.mounts)
                    print(f"  {C.dim(f'Now sharing {total} item(s). Anyone with the page open')}")
                    print(f"  {C.dim('will see it within a few seconds.')}")
        except Exception as e:  # the console must never take the server down with it
            print(f"  {C.bad('✗')} {e}")
