import secrets
import time

from .banner import print_banner
from .fmt import C
from .state import SESSION_TTL, ST


def new_code(length):
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def new_session():
    token = secrets.token_urlsafe(32)
    with ST.lock:
        now = time.time()
        for t, exp in list(ST.sessions.items()):
            if exp < now:
                del ST.sessions[t]
        ST.sessions[token] = now + SESSION_TTL
    return token


def valid_session(token):
    if not token:
        return False
    with ST.lock:
        exp = ST.sessions.get(token)
        if exp is None:
            return False
        if exp < time.time():
            del ST.sessions[token]
            return False
    return True


def lock_left(ip):
    with ST.lock:
        rec = ST.fails.get(ip)
        if not rec:
            return 0
        return max(0, int(rec[1] - time.time()))


def check_code(ip, given):
    """True kalau kodenya bener. Salah -> kunci per-IP naik bertingkat."""
    if lock_left(ip):
        return False
    ok = bool(given) and secrets.compare_digest(str(given), ST.code)
    if ok:
        with ST.lock:
            ST.fails.pop(ip, None)
        return True

    rotated = False
    with ST.lock:
        rec = ST.fails.setdefault(ip, [0, 0.0, 0])
        rec[0] += 1
        ST.global_fails += 1
        if rec[0] >= 5:
            rec[2] += 1
            wait = min(60 * (2 ** (rec[2] - 1)), 900)
            rec[1] = time.time() + wait
            rec[0] = 0
            print(
                f"\n  {C.warn('⚠')} 5x kode salah dari {C.bold(ip)} - dikunci {wait // 60}m {wait % 60}s\n",
                flush=True,
            )
        # Di LAN, ganti IP itu gampang, jadi kunci per-IP doang bisa diakalin.
        if ST.global_fails >= 50:
            ST.global_fails = 0
            ST.code = new_code(ST.cfg.code_len)
            rotated = True
    if rotated:
        print(f"\n  {C.warn('⚠')} Kebanyakan tebakan salah. Kode diganti otomatis.\n", flush=True)
        print_banner(reprint=True)
    return False
