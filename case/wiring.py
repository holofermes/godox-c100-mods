"""Wiring picture: each board as you hold it to solder, each pad used named as it is on the board
and marked with its wire's number; both ends of a wire carry the same number. Wires 1 to 4 run
between the halves through one 4-pin connector.

    uv run python wiring.py        -> images/wiring.svg, and the wire table as markdown

Board outlines and pad positions come from parts.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

CASE = Path(__file__).resolve().parent
sys.path.insert(0, str(CASE))

from lib.parts import PartsDB  # noqa: E402

S = 8.0  # px per mm
W, H = 1060, 820
COLOURS = {
    "5V": "#d62728",
    "GND": "#222222",
    "D+": "#2ca02c",
    "D-": "#17a2b8",
    "3V3": "#ff7f0e",
    "SDA": "#1f77b4",
    "SCL": "#9467bd",
    "BAT": "#e377c2",
}

# number, net, (board, pad) at each end, through the connector
WIRES = [
    (1, "5V", ("USB-C plug board", "VBUS"), ("power board", "USB-A pad 5V"), True),
    (2, "GND", ("USB-C plug board", "GND"), ("power board", "USB-A pad GND"), True),
    (3, "5V", ("Pi", "PP1 (5V)"), ("power board", "header pin 5 (5V)"), True),
    (4, "GND", ("Pi", "PP6 (GND)"), ("power board", "header pin 4 (GND)"), True),
    (5, "D+", ("USB-C plug board", "D+"), ("Pi", "PP22 (D+)"), False),
    (6, "D-", ("USB-C plug board", "D-"), ("Pi", "PP23 (D-)"), False),
    (7, "3V3", ("screen", "VCC"), ("Pi", "GPIO pin 1 (3V3)"), False),
    (8, "SDA", ("screen", "SDA"), ("Pi", "GPIO pin 3 (SDA)"), False),
    (9, "SCL", ("screen", "SCL"), ("Pi", "GPIO pin 5 (SCL)"), False),
    (10, "GND", ("screen", "GND"), ("Pi", "GPIO pin 6 (GND)"), False),
    (11, "BAT", ("cell", "+"), ("power board", "header pin 3 (BAT)"), False),
    (12, "GND", ("cell", "-"), ("power board", "header pin 2 (GND)"), False),
]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rect(x, y, w, h, fill="none", stroke="#555", dash=False, r=0.0, sw=1.2):
    da = ' stroke-dasharray="4 3"' if dash else ""
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r:.1f}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{da}/>'
    )


def text(x, y, s, size=12, anchor="middle", colour="#222", weight="normal", rot=0):
    tr = f' transform="rotate({rot} {x:.1f} {y:.1f})"' if rot else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
        f'font-weight="{weight}" fill="{colour}"{tr}>{esc(s)}</text>'
    )


def hole(x, y, r=4.0):
    return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="#fff" stroke="#888"/>'


def badge(n, net, pad, at, label="", lrot=0, lpos="below"):
    """Wire n's mark: a leader from the pad to a numbered disc at `at`, its name beside it."""
    c = COLOURS[net]
    (px, py), (bx, by) = pad, at
    out = [
        f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4.5" fill="{c}"/>',
        f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{bx:.1f}" y2="{by:.1f}" stroke="{c}" '
        f'stroke-width="2"/>',
        f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="9" fill="{c}"/>',
        text(bx, by + 4, str(n), 11, colour="#fff", weight="bold"),
    ]
    if label:
        if lpos == "below":
            out.append(text(bx + 4, by + 16, label, 11, "start", c, "bold", lrot or 90))
        elif lpos == "right":
            out.append(text(bx + 14, by + 4, label, 11, "start", c, "bold"))
        elif lpos == "above":
            out.append(text(bx - 4, by - 14, label, 11, "start", c, "bold", lrot or -90))
        elif lpos == "left":
            out.append(text(bx - 14, by + 4, label, 11, "end", c, "bold"))
    return out


def short(board: str, pad: str) -> str:
    """A pad as the legend names it."""
    b = {
        "USB-C plug board": "plug",
        "power board": "power",
        "screen": "screen",
        "cell": "cell",
        "Pi": "Pi",
    }
    pad = (
        pad.replace("USB-A pad ", "USB-A ")
        .replace("header pin ", "pin ")
        .replace("GPIO pin ", "pin ")
    )
    return f"{b[board]} {pad}"


class Face:
    """A board face drawn with its frame's (x0, y_top) corner at (ox, oy) px: x right as seen
    (`flip_x` mirrors it, a face seen from the other side), y down the page from y_top."""

    def __init__(self, ox, oy, x0, x1, y_top, y_down=True, flip_x=False):
        self.ox, self.oy, self.x0, self.x1 = ox, oy, x0, x1
        self.y_top, self.y_down, self.flip_x = y_top, y_down, flip_x

    def p(self, x, y):
        u = (self.x1 - x) if self.flip_x else (x - self.x0)
        v = (y - self.y_top) if self.y_down else (self.y_top - y)
        return self.ox + u * S, self.oy + v * S

    def box(self, x0, x1, y0, y1):
        (a, b), (c, d) = self.p(x0, y0), self.p(x1, y1)
        return min(a, c), min(b, d), abs(c - a), abs(d - b)


def main() -> int:
    db = PartsDB(CASE / "parts.json")
    pz = db.get(
        "pi_zero_w",
        ("board", "gpio_header", "test_pads", "mini_hdmi", "usb_data", "usb_power", "microsd"),
    )["features"]
    pb = db.get("lipo_rider_plus", ("pcb", "usb_c", "jst", "switch", "leds", "header", "out_5v"))[
        "features"
    ]
    pl = db.get("usb_c_plug_ada5978", ("board", "shell", "pins"))["features"]
    out = []

    # --- back half ---------------------------------------------------------------------------
    out.append(text(300, 30, "BACK HALF", 15, colour="#666", weight="bold"))

    # the plug board from the front (its labels' side), the plug up
    b, sh, pins = pl["board"], pl["shell"], pl["pins"]
    plug = Face(330, 60, b["x0"], b["x1"], sh["z1"], y_down=False, flip_x=True)
    x, y, w, h = plug.box(b["x0"], b["x1"], b["z1"], b["z0"])
    out.append(rect(x, y, w, h, "#e9e9e9", "#222", sw=2))
    sx, sy, sw_, shh = plug.box(sh["x0"], sh["x1"], sh["z1"], sh["z0"])
    out += [
        rect(sx, sy, sw_, shh, "#cfcfcf", "#555", r=4),
        text(sx + sw_ / 2, sy + 20, "USB-C", 10),
    ]
    out.append(text(x - 14, y + 30, "USB-C plug board", 13, "end", weight="bold"))
    out.append(text(x - 14, y + 46, "front (the side with its labels),", 11, "end"))
    out.append(text(x - 14, y + 61, "plug up into the camera", 11, "end"))
    pp = {}
    for name, (px_, pz_) in pins["points_xz"].items():
        cx, cy = plug.p(px_, pz_)
        out.append(hole(cx, cy))
        out.append(text(cx + 3, cy - 10, name, 8, "start", "#555", rot=-90))
        pp[name] = (cx, cy)
    out.append(text(x - 14, y + 80, "470 µF across VBUS and GND", 11, "end", "#444"))

    # the Zero from below (the side with no parts), its ports' edge up
    bd, g, tp = pz["board"], pz["gpio_header"], pz["test_pads"]
    zero = Face(40, 330, 0.0, bd["x"], 0.0)  # y runs down from the ports' edge
    x, y, w, h = zero.box(0, bd["x"], 0, bd["y"])
    out.append(rect(x, y, w, h, "#e6f2e6", "#2f7d32", r=bd["corner_r"] * S, sw=2))
    out.append(
        text(
            x + 230,
            y + h + 30,
            "Pi Zero W, from below (the side with no parts),",
            13,
            "start",
            weight="bold",
        )
    )
    out.append(text(x + 230, y + h + 46, "its USB and HDMI ports at the top edge", 11, "start"))
    for k, lab in (("mini_hdmi", "mini HDMI"), ("usb_data", "USB"), ("usb_power", "PWR IN")):
        v = pz[k]
        rx, ry, rw, rh = zero.box(v["x0"], v["x1"], max(v["y0"], 0.0), v["y1"])
        out += [
            rect(rx, ry, rw, rh, dash=True),
            text(rx + rw / 2, ry + rh + 13, lab, 10, colour="#666"),
        ]
    sd = pz["microsd"]["socket"]
    rx, ry, rw, rh = zero.box(sd["x0"], sd["x1"], sd["y0"], sd["y1"])
    out += [
        rect(rx, ry, rw, rh, dash=True),
        text(rx + rw / 2, ry + rh / 2 + 4, "microSD", 10, colour="#666"),
    ]

    def gpio(k):
        return zero.p(
            g["pin1"][0] + g["pitch"] * ((k - 1) // 2),
            g["pin1"][1] + (g["pitch"] if k % 2 == 0 else 0.0),
        )

    for k in range(1, 41):
        out.append(hole(*gpio(k), 3.2))
    gx, gy = gpio(39)
    out.append(text(gx, gy - 12, "GPIO header", 10, colour="#666"))
    tpp = {n: zero.p(*xy) for n, xy in tp["points"].items()}
    for xy in tpp.values():
        out.append(hole(*xy, 3.5))

    # the screen module's cable
    scr = (270, 740)
    out.append(rect(scr[0], scr[1], 150, 44, "#f2f2f2", "#333", r=4, sw=2))
    out.append(text(scr[0] + 75, scr[1] + 20, "screen module", 12, weight="bold"))
    out.append(text(scr[0] + 75, scr[1] + 35, "its cable's wires", 10))

    # --- the connector -------------------------------------------------------------------------
    con = (690, 580)
    out.append(rect(con[0], con[1], 150, 44, "#fafafa", "#333", r=4, sw=2))
    out.append(
        text(con[0] + 75, con[1] - 10, "4-pin connector between the halves", 11, weight="bold")
    )
    for i in range(4):
        out.append(text(con[0] + 22 + i * 35, con[1] + 27, str(i + 1), 13, weight="bold"))
    out.append(
        text(con[0] + 75, con[1] + 62, "wires 1 to 4 run through it, pin = wire", 10, colour="#444")
    )

    # --- front half ----------------------------------------------------------------------------
    out.append(text(820, 30, "FRONT HALF", 15, colour="#666", weight="bold"))
    pcb, hdr = pb["pcb"], pb["header"]
    pwr = Face(650, 160, pcb["x0"], pcb["x1"], pcb["y1"], y_down=False)  # header edge (y 0) down
    x, y, w, h = pwr.box(pcb["x0"], pcb["x1"], pcb["y1"], pcb["y0"])
    out.append(rect(x, y, w, h, "#e6ecf7", "#1f3f8f", r=pcb["r"] * S, sw=2))
    out.append(text(x, y - 34, "power board, parts side,", 13, "start", weight="bold"))
    out.append(text(x, y - 18, "USB-C at the left, the pin header along the bottom;", 11, "start"))
    out.append(text(x, y - 4, "USB-A: the USB-A socket's pads, the socket taken off", 11, "start"))
    for k, lab in (("usb_c", "USB-C"), ("jst", "JST (empty)"), ("switch", "5V switch")):
        v = pb[k]
        rx, ry, rw, rh = pwr.box(v["x0"], v["x1"], v["y1"], v["y0"])
        out += [rect(rx, ry, rw, rh, "#fff", "#777"), text(rx + rw / 2, ry + rh / 2 + 4, lab, 9)]
    hp = {}
    for i, xy in enumerate(hdr["points_xy"], 1):
        hp[i] = pwr.p(*xy)
        out.append(hole(*hp[i]))
    out.append(text(hp[1][0], hp[1][1] - 9, "1", 9, colour="#555"))
    out.append(text(hp[8][0], hp[8][1] - 9, "8", 9, colour="#555"))
    ua = {n: pwr.p(*xy) for n, xy in pb["out_5v"]["pads"].items()}
    for xy in ua.values():
        out.append(hole(*xy, 5))

    cell = (690, 470)
    out.append(rect(cell[0], cell[1], 200, 44, "#f2f2f2", "#333", r=8, sw=2))
    out.append(text(cell[0] + 100, cell[1] + 27, "LiPo cell", 12, weight="bold"))

    # --- the wires' marks ----------------------------------------------------------------------
    bot = y + h  # the power board's bottom edge
    marks = []
    marks += badge(
        1, "5V", pp["VBUS"], (pp["VBUS"][0] - 30, pp["VBUS"][1] + 34), "VBUS", lpos="left"
    )
    marks += badge(2, "GND", pp["GND"], (pp["GND"][0] + 30, pp["GND"][1] + 34), "GND", lpos="right")
    marks += badge(5, "D+", pp["D+"], (pp["D+"][0] - 4, pp["D+"][1] + 34))
    marks += badge(6, "D-", pp["D-"], (pp["D-"][0] + 6, pp["D-"][1] + 34))
    t23, t22 = tpp["PP23"], tpp["PP22"]
    marks += badge(6, "D-", t23, (t23[0] - 14, t23[1] - 34), "PP23 D-", lpos="left")
    marks += badge(5, "D+", t22, (t22[0] + 14, t22[1] - 34), "PP22 D+", lpos="right")
    t1, t6 = tpp["PP1"], tpp["PP6"]
    marks += badge(4, "GND", t6, (t6[0] + 70, t6[1] - 8), "PP6 GND", lpos="right")
    marks += badge(3, "5V", t1, (t1[0] + 70, t1[1] + 18), "PP1 5V", lpos="right")
    for n, k, net, lab, dx in (
        (7, 1, "3V3", "pin 1 3V3", -8),
        (8, 3, "SDA", "pin 3 SDA", -4),
        (9, 5, "SCL", "pin 5 SCL", 0),
        (10, 6, "GND", "pin 6 GND", 24),
    ):
        px_, py_ = gpio(k)
        marks += badge(n, net, (px_, py_), (px_ + dx, py_ + 52 - (py_ - gpio(1)[1])), lab)
    for i, (n, net, lab) in enumerate(
        ((7, "3V3", "VCC"), (8, "SDA", "SDA"), (9, "SCL", "SCL"), (10, "GND", "GND"))
    ):
        bx, by = scr[0] + 24 + i * 34, scr[1] - 26
        marks += badge(n, net, (bx, scr[1]), (bx, by))
        marks.append(text(bx, by - 14, lab, 10, colour=COLOURS[net], weight="bold"))
    marks += badge(1, "5V", ua["5V"], (ua["5V"][0] + 50, ua["5V"][1]), "USB-A 5V", lpos="right")
    marks += badge(
        2, "GND", ua["GND"], (ua["GND"][0] + 50, ua["GND"][1]), "USB-A GND", lpos="right"
    )
    for n, k, net, lab in (
        (12, 2, "GND", "2 GND"),
        (11, 3, "BAT", "3 BAT"),
        (4, 4, "GND", "4 GND"),
        (3, 5, "5V", "5 5V"),
    ):
        px_, py_ = hp[k]
        marks += badge(n, net, (px_, py_), (px_, bot + 26), lab)
    for i, (n, net, lab) in enumerate(((11, "BAT", "+"), (12, "GND", "-"))):
        bx, by = cell[0] + 70 + i * 60, cell[1] + 44 + 26
        marks += badge(n, net, (bx, cell[1] + 44), (bx, by))
        marks.append(text(bx + 16, by + 5, lab, 14, "start", colour=COLOURS[net], weight="bold"))
    for i, net in enumerate(("5V", "GND", "5V", "GND")):
        marks.append(
            f'<circle cx="{con[0] + 22 + i * 35:.1f}" cy="{con[1] + 40:.1f}" r="3" fill="{COLOURS[net]}"/>'
        )

    lx, ly = 620, 690
    for i, (n, net, (fb, fp), (tb, tpad), _con) in enumerate(WIRES):
        col, row = divmod(i, 6)
        bx, by = lx + col * 210, ly + row * 22
        marks.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="8" fill="{COLOURS[net]}"/>')
        marks.append(text(bx, by + 4, str(n), 10, colour="#fff", weight="bold"))
        marks.append(text(bx + 14, by + 4, f"{short(fb, fp)} → {short(tb, tpad)}", 10, "start"))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Helvetica, Arial, sans-serif">\n'
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>\n' + "\n".join(out + marks) + "\n</svg>\n"
    )
    img = CASE / "images"
    img.mkdir(exist_ok=True)
    (img / "wiring.svg").write_text(svg)
    print(f"wiring: wrote {img / 'wiring.svg'}")
    print()
    print("| wire | from | to |")
    print("|---|---|---|")
    for n, _net, (fb, fp), (tb, tpad), con_ in WIRES:
        via = ", through the connector" if con_ else ""
        print(f"| {n} | {fb}: {fp} | {tb}: {tpad}{via} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
