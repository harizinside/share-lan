import os
import urllib.parse

from .fmt import C, human
from .mounts import dir_stats
from .network import local_hostname
from .qr import qr_ascii
from .state import ST


def share_url():
    if ST.single_file:
        return f"{ST.base_url}/dl?p={urllib.parse.quote(ST.single_file)}&dl=1&code={ST.code}"
    return f"{ST.base_url}/?code={ST.code}"


def print_banner(reprint=False):
    cfg = ST.cfg
    print()
    if not reprint:
        print(f"  {C.accent('◆')} {C.bold('share·lan')}  {C.dim('— nyala. Ctrl-C buat matiin.')}")
        print()
        if not ST.mounts:
            print(
                f"  {C.warn('Belum ada yang dibagikan')} {C.dim('— nggak ada file yang kebuka.')}"
            )
            print(f"  {C.dim('Seret file/folder dari Finder ke jendela ini, terus Enter.')}")
        else:
            print(f"  {C.dim('Dibagikan:')} {len(ST.mounts)} item")
        for name, full in list(ST.mounts.items())[:12]:
            count, size, cut = dir_stats(full, budget=0.6)
            if os.path.isdir(full):
                extra = f"{count}{'+' if cut else ''} file, {human(size)}{'+' if cut else ''}"
                print(f"    {C.accent('•')} {name}/  {C.dim('(' + extra + ')')}")
            else:
                print(f"    {C.accent('•')} {name}  {C.dim('(' + human(size) + ')')}")
        if len(ST.mounts) > 12:
            print(f"    {C.dim('… dan ' + str(len(ST.mounts) - 12) + ' lagi')}")
        print()
    print(f"  {C.dim('Alamat')} : {C.bold(ST.base_url)}   {C.dim('← dipakai di QR')}")
    alt = local_hostname(cfg.port)
    if alt:
        print(f"  {C.dim('Juga')}   : {alt}  {C.dim('(tahan ganti IP)')}")
    print(f"  {C.dim('Kode')}   : {C.bold(C.accent(ST.code))}")
    if not cfg.no_qr:
        print()
        print(qr_ascii(share_url()))
    print()
    print(
        f"  {C.dim('→ Ke HP')}    : scan QR di atas"
        + (f" {C.dim('(langsung download filenya)')}" if ST.single_file else "")
    )
    print(f"  {C.dim('→ Ke laptop')}: kasih alamat + kode di atas")
    others = [x for x in ST.addresses[1:] if x[2] > -900][:3]
    if others:
        alts = ", ".join(f"{ip}{' (' + ifc + ')' if ifc else ''}" for ip, ifc, _ in others)
        print()
        print(f"  {C.warn('⚠')} {C.dim('Nggak kebuka dari HP? Coba alamat lain:')} {alts}")
    print()
