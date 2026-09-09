"""Update the current installation without invoking a remote shell script."""

import importlib.util
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError, distribution, version

REPO = "harizinside/share-lan"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"


def installed_version():
    try:
        return version("share-lan")
    except PackageNotFoundError:
        return "0.0.4"


def latest_release_tag():
    """The newest stable release tag on GitHub - not `main` HEAD, which may be untested."""
    req = urllib.request.Request(
        LATEST_RELEASE_API, headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    tag = data.get("tag_name")
    if not tag:
        raise ValueError("GitHub release response has no tag_name")
    return tag


def update():
    try:
        direct_url = json.loads(distribution("share-lan").read_text("direct_url.json") or "{}")
    except PackageNotFoundError:
        print("Not installed yet. Run the installer from the README first.", file=sys.stderr)
        return 1
    if direct_url.get("dir_info", {}).get("editable"):
        print("Editable install: run git pull in the repo, then uv sync.", file=sys.stderr)
        return 1

    try:
        tag = latest_release_tag()
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        print(f"Failed to fetch the latest release from GitHub: {exc}", file=sys.stderr)
        return 1
    source_url = f"https://github.com/{REPO}/archive/refs/tags/{tag}.tar.gz"

    if importlib.util.find_spec("pip") is not None:
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            source_url,
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
            source_url,
        ]
    else:
        print("pip/uv not found. Re-run the installer from the README.", file=sys.stderr)
        return 1

    print(f"Updating sharelan {installed_version()} -> {tag} from GitHub…", flush=True)
    try:
        result = subprocess.run(command, check=False)
    except OSError as exc:
        print(f"Failed to run the updater: {exc}", file=sys.stderr)
        return 1
    if result.returncode:
        print("Update failed. Check the output above, then try again.", file=sys.stderr)
        return result.returncode
    print("Update complete. Check the version with sharelan --version.")
    return 0
