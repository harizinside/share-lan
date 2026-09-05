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
        print(f"  {C.accent('◆')} {C.bold('share·lan')}  {C.dim('— running. Ctrl-C to stop.')}")
        print()
        if not ST.mounts:
            print(f"  {C.warn('Nothing shared yet')} {C.dim('— no files are exposed.')}")
            print(
                f"  {C.dim('Drag a file/folder from Finder into this window, then press Enter.')}"
            )
        else:
            print(f"  {C.dim('Shared:')} {len(ST.mounts)} item(s)")
        for name, full in list(ST.mounts.items())[:12]:
            count, size, cut = dir_stats(full, budget=0.6)
            if os.path.isdir(full):
                extra = f"{count}{'+' if cut else ''} file, {human(size)}{'+' if cut else ''}"
                print(f"    {C.accent('•')} {name}/  {C.dim('(' + extra + ')')}")
            else:
                print(f"    {C.accent('•')} {name}  {C.dim('(' + human(size) + ')')}")
        if len(ST.mounts) > 12:
            print(f"    {C.dim('… and ' + str(len(ST.mounts) - 12) + ' more')}")
        print()
    print(f"  {C.dim('Address')} : {C.bold(ST.base_url)}   {C.dim('← used in the QR code')}")
    alt = local_hostname(cfg.port)
    if alt:
        print(f"  {C.dim('Also')}    : {alt}  {C.dim('(survives IP changes)')}")
    print(f"  {C.dim('Code')}    : {C.bold(C.accent(ST.code))}")
    if not cfg.no_qr:
        print()
        print(qr_ascii(share_url()))
    print()
    print(
        f"  {C.dim('→ On phone')} : scan the QR code above"
        + (f" {C.dim('(downloads the file directly)')}" if ST.single_file else "")
    )
    print(f"  {C.dim('→ On laptop')}: give them the address + code above")
    others = [x for x in ST.addresses[1:] if x[2] > -900][:3]
    if others:
        alts = ", ".join(f"{ip}{' (' + ifc + ')' if ifc else ''}" for ip, ifc, _ in others)
        print()
        print(f"  {C.warn('⚠')} {C.dim('Not loading on the phone? Try another address:')} {alts}")
    print()
