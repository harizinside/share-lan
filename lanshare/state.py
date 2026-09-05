import threading

APP = "lanshare"
CHUNK = 1 << 20
ZIP64_LIMIT = 0xFFFFFFFF
SESSION_TTL = 86400
THUMB_CACHE_MAX = 500  # entries; ~30KB/thumb -> ~15MB upper bound
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff", ".avif"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".opus"}
INLINE_EXT = IMAGE_EXT | VIDEO_EXT | AUDIO_EXT | {".pdf", ".txt", ".md"}
WIN_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class State:
    """Everything shared across threads, collected in one place."""

    def __init__(self):
        self.cfg = None
        self.mounts = {}  # display name -> absolute path on disk
        self.code = ""
        self.sessions = {}  # token -> expiry time
        self.fails = {}  # ip -> [failures, locked_until, round, last_attempt]
        self.global_fails = 0
        self.initial_path = ""
        self.single_file = None
        self.base_url = ""
        self.lock = threading.Lock()
        self.crc_cache = {}  # (path, size, mtime) -> crc32
        self.hash_cache = {}  # (path, size, mtime) -> sha256
        self.thumb_cache = {}  # (path, size, mtime, box) -> (bytes, content_type)
        self.preview_cache = {}  # (path, size, mtime, "gdocs") -> (bytes html, page count)
        self.zip_plans = {}  # path -> (content signature, segments, size)
        self.single_root = False  # only 1 folder shared -> that folder becomes the root
        self.addresses = []  # [(ip, iface, score)]
        self.revision = 0  # bumped whenever the shared list changes; clients poll this


ST = State()
