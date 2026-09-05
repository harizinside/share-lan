import io

import qrcode


def qr_matrix(data):
    q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(data)
    q.make(fit=True)
    return q


def qr_svg(data, box=8):
    m = qr_matrix(data).get_matrix()
    n = len(m)
    side = n * box
    rects = []
    for y, row in enumerate(m):  # merge adjacent boxes in a row into a single rect
        x = 0
        while x < n:
            if row[x]:
                start = x
                while x < n and row[x]:
                    x += 1
                rects.append(
                    f'<rect x="{start * box}" y="{y * box}" width="{(x - start) * box}" height="{box}"/>'
                )
            else:
                x += 1
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{side}" height="{side}" '
        f'viewBox="0 0 {side} {side}" shape-rendering="crispEdges">'
        f'<rect width="{side}" height="{side}" fill="#fff"/>'
        f'<g fill="#000">{"".join(rects)}</g></svg>'
    ).encode()


def qr_ascii(data):
    buf = io.StringIO()
    qr_matrix(data).print_ascii(out=buf, invert=True)
    return buf.getvalue().rstrip("\n")
