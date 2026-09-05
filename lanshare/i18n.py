"""Translation table for the recipient-facing web UI (English default, other languages optional).

CLI/console/banner output is plain English with no switching — this module only covers text that
reaches a browser: the SPA in pages.py and the error/login messages httpserver.py sends back.

Translations live in lanshare/locales/<code>.json as flat {key: string} files, one per language —
see that directory's own comment for how to add a new one. English (locales/en.json) is the
required, complete reference; every other file may be partial, since any key it's missing just
falls back to the English string.
"""

import json
from pathlib import Path

DEFAULT_LANG = "en"

_LOCALES_DIR = Path(__file__).parent / "locales"


def _load_locales():
    table = {}
    for path in sorted(_LOCALES_DIR.glob("*.json")):
        table[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return table


_TABLE = _load_locales()
LANGS = tuple(_TABLE) or (DEFAULT_LANG,)


def t(key, lang=DEFAULT_LANG, **kwargs):
    en = _TABLE.get(DEFAULT_LANG, {})
    s = _TABLE.get(lang, {}).get(key, en.get(key, key))
    return s.format(**kwargs) if kwargs else s


def get_lang(cookie_header):
    """Parse the `sl_lang` cookie out of a raw Cookie header; default to DEFAULT_LANG."""
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "sl_lang" and v in LANGS:
            return v
    return DEFAULT_LANG


def flat_table():
    """{'en': {key: str, ...}, ...} for embedding into the page as JSON."""
    return _TABLE
