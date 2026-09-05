"""Update the current installation without invoking a remote shell script."""

import importlib.util
import json
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution, version

SOURCE_URL = "https://github.com/harizinside/share-lan/archive/refs/heads/main.tar.gz"


def installed_version():
    try:
        return version("share-lan")
    except PackageNotFoundError:
        return "0.0.2"


def update():
    try:
        direct_url = json.loads(distribution("share-lan").read_text("direct_url.json") or "{}")
    except PackageNotFoundError:
        print("Belum terinstall. Jalankan installer dari README dulu.", file=sys.stderr)
        return 1
    if direct_url.get("dir_info", {}).get("editable"):
        print("Instalasi editable: jalankan git pull di repo, lalu uv sync.", file=sys.stderr)
        return 1

    if importlib.util.find_spec("pip") is not None:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            SOURCE_URL,
        ]
    elif shutil.which("uv"):
        command = [
            shutil.which("uv"),
            "pip",
            "install",
            "--python",
            sys.executable,
            "--reinstall-package",
            "share-lan",
            SOURCE_URL,
        ]
    else:
        print("pip/uv nggak ketemu. Jalankan ulang installer dari README.", file=sys.stderr)
        return 1

    print(f"Update sharelan {installed_version()} dari GitHub main…", flush=True)
    try:
        result = subprocess.run(command, check=False)
    except OSError as exc:
        print(f"Gagal menjalankan updater: {exc}", file=sys.stderr)
        return 1
    if result.returncode:
        print("Update gagal. Periksa output di atas, lalu coba lagi.", file=sys.stderr)
        return result.returncode
    print("Update selesai. Cek versi dengan sharelan --version.")
    return 0
