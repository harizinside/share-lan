import argparse
import os
import re
import sys
import time


def human(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def parse_size(text):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?)b?\s*", str(text), re.I)
    if not m:
        raise argparse.ArgumentTypeError(f"ukuran nggak kebaca: {text!r} (contoh: 500M, 20G)")
    mult = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30, "t": 1 << 40}[m.group(2).lower()]
    return int(float(m.group(1)) * mult)


class C:
    """Warna ANSI, mati sendiri kalau output-nya di-pipe."""

    on = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    @classmethod
    def _w(cls, code, s):
        return f"\033[{code}m{s}\033[0m" if cls.on else str(s)

    @classmethod
    def dim(cls, s):
        return cls._w("2", s)

    @classmethod
    def bold(cls, s):
        return cls._w("1", s)

    @classmethod
    def accent(cls, s):
        return cls._w("38;5;141", s)

    @classmethod
    def ok(cls, s):
        return cls._w("38;5;114", s)

    @classmethod
    def warn(cls, s):
        return cls._w("38;5;214", s)

    @classmethod
    def bad(cls, s):
        return cls._w("38;5;203", s)


def log(msg):
    print(f"{C.dim(time.strftime('%H:%M:%S'))}  {msg}", flush=True)


def die(msg):
    print(f"\n  {C.bad('✗')} {msg}\n", file=sys.stderr)
    sys.exit(1)
