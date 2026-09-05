"""LAN Share - drop any path, anyone else just scans & downloads.

    uv run lanshare ~/Desktop/video.mp4 ~/Holiday\\ Photos/

Drag a file/folder from Finder into the terminal to paste its path.
Recipients open the IP + a 4-digit code, or just scan the QR.
"""

from .auth import check_code, lock_left, new_code, new_session, valid_session
from .banner import print_banner, share_url
from .cli import bind_server, configure, keep_awake, main, parse_args
from .console import console_available, console_loop, print_shared
from .fmt import C, die, human, log, parse_size
from .httpserver import Handler, Server, parse_range, unique_path
from .i18n import DEFAULT_LANG, LANGS, get_lang, t
from .mounts import (
    Denied,
    Missing,
    add_paths,
    build_mounts,
    can_upload_here,
    dir_stats,
    entry_of,
    kind_of,
    list_entries,
    recompute,
    remove_mount,
    resolve,
    safe_upload_name,
    total_stats,
    unique_name,
    visible,
    walk_files,
)
from .network import find_addresses, local_hostname
from .pages import login_page, page_html
from .preview import DOC_EXT, HAVE_GROUPDOCS, previewable, render_html
from .qr import qr_ascii, qr_matrix, qr_svg
from .state import ST, State
from .thumb import HAVE_PIL, crc32_of, file_key, make_thumb, sha256_of
from .ziputil import Stale, StreamWriter, build_zip_plan, emit_plan, zip_entries, zip_name

__all__ = [
    "C",
    "DEFAULT_LANG",
    "Denied",
    "DOC_EXT",
    "HAVE_PIL",
    "HAVE_GROUPDOCS",
    "Handler",
    "LANGS",
    "Missing",
    "ST",
    "Server",
    "Stale",
    "State",
    "StreamWriter",
    "add_paths",
    "bind_server",
    "build_mounts",
    "build_zip_plan",
    "can_upload_here",
    "check_code",
    "configure",
    "console_available",
    "console_loop",
    "crc32_of",
    "die",
    "dir_stats",
    "emit_plan",
    "entry_of",
    "file_key",
    "find_addresses",
    "get_lang",
    "human",
    "keep_awake",
    "kind_of",
    "list_entries",
    "local_hostname",
    "lock_left",
    "log",
    "login_page",
    "main",
    "make_thumb",
    "new_code",
    "new_session",
    "page_html",
    "parse_args",
    "parse_range",
    "parse_size",
    "previewable",
    "print_banner",
    "print_shared",
    "qr_ascii",
    "qr_matrix",
    "qr_svg",
    "render_html",
    "recompute",
    "remove_mount",
    "resolve",
    "safe_upload_name",
    "sha256_of",
    "share_url",
    "t",
    "total_stats",
    "unique_name",
    "unique_path",
    "valid_session",
    "visible",
    "walk_files",
    "zip_entries",
    "zip_name",
]
