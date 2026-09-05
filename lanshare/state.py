import threading

APP = "lanshare"
CHUNK = 1 << 20
ZIP64_LIMIT = 0xFFFFFFFF
SESSION_TTL = 86400
THUMB_CACHE_MAX = 500  # entri; ~30KB/thumb -> ~15MB batas atas
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
    """Semua yang dibagi antar-thread, dikumpulin di satu tempat."""

    def __init__(self):
        self.cfg = None
        self.mounts = {}  # nama tampilan -> path absolut di disk
        self.code = ""
        self.sessions = {}  # token -> waktu kedaluwarsa
        self.fails = {}  # ip -> [gagal, dikunci_sampai, ronde]
        self.global_fails = 0
        self.initial_path = ""
        self.single_file = None
        self.base_url = ""
        self.lock = threading.Lock()
        self.crc_cache = {}  # (path, size, mtime) -> crc32
        self.hash_cache = {}  # (path, size, mtime) -> sha256
        self.thumb_cache = {}  # (path, size, mtime, box) -> (bytes, content_type)
        self.zip_plans = {}  # path -> (tanda tangan isi, segmen, ukuran)
        self.single_root = False  # cuma 1 folder yang dishare -> folder itu jadi root
        self.addresses = []  # [(ip, iface, skor)]
        self.revision = 0  # naik tiap daftar bagikan berubah; klien polling ini


ST = State()
