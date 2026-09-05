import ipaddress
import re
import socket
import subprocess

VPNISH = re.compile(r"^(utun|tun|tap|ppp|ipsec|awdl|llw|bridge|docker|vboxnet|vmnet)")
WIRED = re.compile(r"^(en|eth|wl|wlan)")


def _iface_addrs():
    """[(ip, nama_interface)] dari ifconfig / ip addr. Kosong kalau nggak ada dua-duanya."""
    for cmd in (["ifconfig", "-a"], ["ip", "-4", "-o", "addr"]):
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=4).stdout.decode(
                "utf-8", "replace"
            )
        except Exception:
            continue
        found, iface = [], None
        for line in out.splitlines():
            m = re.match(r"^(\w[\w.:-]*):", line)
            if m:
                iface = m.group(1)
            m2 = re.search(r"\binet (?:addr:)?(\d+\.\d+\.\d+\.\d+)", line)
            if m2:
                name = iface
                m3 = re.match(r"^\d+:\s*(\S+)", line)  # format `ip -o`
                if m3:
                    name = m3.group(1)
                found.append((m2.group(1), name or ""))
        if found:
            return found
    return []


def _route_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # nggak ngirim paket, cuma minta routing table nunjuk interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _score(ip, iface, is_route):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return -999
    if addr.is_loopback or addr.is_link_local:
        return -999
    s = 0
    if ip.startswith("192.168."):
        s += 100
    elif ip.startswith("10."):
        s += 90
    elif addr.is_private:
        s += 80
    else:
        s -= 40
    if iface and VPNISH.match(iface):
        s -= 70
    elif iface and WIRED.match(iface):
        s += 20
    if is_route:
        s += 25
    return s


def find_addresses():
    """[(ip, iface, skor)] urut dari yang paling mungkin kepakai."""
    route = _route_ip()
    seen = {}
    for ip, iface in _iface_addrs():
        seen.setdefault(ip, iface)
    if route:
        seen.setdefault(route, "")
    if not seen:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                seen.setdefault(info[4][0], "")
        except OSError:
            pass
    out = [(ip, ifc, _score(ip, ifc, ip == route)) for ip, ifc in seen.items()]
    out = [x for x in out if x[2] > -900]
    out.sort(key=lambda x: -x[2])
    return out


def local_hostname(port):
    try:
        name = socket.gethostname()
    except OSError:
        return None
    if not name:
        return None
    if not name.endswith(".local"):
        name += ".local"
    try:
        socket.getaddrinfo(name, None, socket.AF_INET)
    except OSError:
        return None
    return f"http://{name}:{port}"
