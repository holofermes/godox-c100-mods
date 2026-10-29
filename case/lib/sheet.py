"""Contact sheets: the renders of a build (six views, then one per pose) plus its sizes and
checks in sheet.png, the text panel growing with its lines; the close-ups, if rendered, in
closeups.png (one column per close-up, with the camera above, without below).

uv run python -m lib.sheet out/<design>-NN
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

VIEWS = [
    ("front34", "3/4 front"),
    ("back34", "3/4 back"),
    ("side", "side, +X"),
    ("top", "top"),
    ("exploded", "exploded"),
    ("xray", "x-ray"),
]
CELL = 620
HEAD = 64
LINE = 22  # text panel line height
FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
]
MONO = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
]


def font(paths, size):
    for p in paths:
        if Path(p).is_file():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def fmt(v):
    return " x ".join(f"{x:.1f}" for x in v)


def lines_for(build: Path) -> list[str]:
    sizes = json.loads((build / "sizes.json").read_text())
    checks = json.loads((build / "checks.json").read_text())
    cfg = json.loads((build / "config.json").read_text())
    real = [ln for ln in checks["lines"] if not ln.get("info")]
    passed = sum(1 for ln in real if ln["pass"])
    out = [
        f"checks {passed}/{len(real)} pass"
        + ("" if checks["pass"] else "  FAILED: ")
        + ", ".join(f"{ln['check']} {ln['name']}" for ln in real if not ln["pass"]),
        *(f"check off: {ln['check']}, {ln['detail']}" for ln in checks["lines"] if ln.get("off")),
        f"case {fmt(sizes['case_overall']['size'])} mm, with camera {fmt(sizes['assembly_overall']['size'])} mm (X x Y x Z)",
        "",
        "printed bodies (as printed, A1 mini 180 / 256):",
    ]
    bed = {ln["name"]: ln["pass"] for ln in checks["lines"] if ln["check"] == "bed"}
    for name, b in sizes["bodies"].items():
        small = bed.get(f"{name} on A1 mini (180)")
        out.append(
            f"  {name:<14} {fmt(b['as_printed']):<20} {'fits' if small else 'TOO BIG'} A1 mini, "
            f"{b['volume_cm3']:.1f} cm3, {b['note']}"
        )
    out.append("")
    est = [n for n, p in sizes["parts"].items() if p["estimate"]]
    out.append("estimates and stand-ins: " + ", ".join(est))
    out.append("hardware: " + "; ".join(f"{h['qty']}x {h['item']}" for h in sizes["hardware"]))
    for n in sizes.get("notes", []):
        out.append(n)
    if cfg["overrides"]:
        out.append("overrides: " + ", ".join(f"{k}={cfg['values'][k]}" for k in cfg["overrides"]))
    return out


def wrap(draw, text, fnt, width):
    if draw.textlength(text, font=fnt) <= width:
        return [text]
    words, line, out = text.split(" "), "", []
    for w in words:
        t = (line + " " + w).strip()
        if draw.textlength(t, font=fnt) > width and line:
            out.append(line)
            line = "    " + w
        else:
            line = t
    out.append(line)
    return out


def views_for(build: Path) -> list[tuple[str, str]]:
    """The six views, then the pose views that were rendered."""
    out = list(VIEWS)
    rj = build / "render.json"
    poses = json.loads(rj.read_text()).get("poses", []) if rj.is_file() else []
    for i, ps in enumerate(poses, 1):
        if (build / "render" / f"pose{i}.png").is_file():
            out.append((f"pose{i}", ps["name"]))
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    build = Path(argv[0])
    cfg = json.loads((build / "config.json").read_text())
    W = CELL * 3
    title = font(FONTS, 30)
    cap = font(FONTS, 20)
    mono = font(MONO, 17)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    text = [part for ln in lines_for(build) for part in wrap(measure, ln, mono, W - 40)]
    views = views_for(build)
    rows = math.ceil(len(views) / 3)
    H = HEAD + CELL * rows + 14 + LINE * len(text) + 14
    sheet = Image.new("RGB", (W, H), (236, 236, 234))
    d = ImageDraw.Draw(sheet)
    d.rectangle([0, 0, W, HEAD], fill=(30, 30, 32))
    d.text((20, 15), f"{build.name}", font=title, fill=(255, 170, 60))
    d.text((260, 22), f"{cfg['time']}   git {cfg['git']}", font=cap, fill=(200, 200, 200))
    for i, (key, label) in enumerate(views):
        x, y = (i % 3) * CELL, HEAD + (i // 3) * CELL
        p = build / "render" / f"{key}.png"
        if p.is_file():
            im = Image.open(p).convert("RGB")
            im.thumbnail((CELL, CELL))
            sheet.paste(im, (x + (CELL - im.width) // 2, y + (CELL - im.height) // 2))
        d.text((x + 12, y + 8), label, font=cap, fill=(40, 40, 40))
    y = HEAD + CELL * rows + 14
    for part in text:
        d.text((20, y), part, font=mono, fill=(20, 20, 20))
        y += LINE
    out = build / "sheet.png"
    sheet.save(out)
    print(f"sheet: {out}")
    closeups(build, cfg)


def closeups(build: Path, cfg: dict):
    """closeups.png: one column per close-up in render.json, with the camera, then without."""
    rj = build / "render.json"
    cus = json.loads(rj.read_text()).get("closeups", []) if rj.is_file() else []
    cus = [c for c in cus if (build / "render" / f"close-{c['name']}.png").is_file()]
    if not cus:
        return
    W = CELL * len(cus)
    title = font(FONTS, 30)
    cap = font(FONTS, 20)
    sheet = Image.new("RGB", (W, HEAD + 2 * CELL), (236, 236, 234))
    d = ImageDraw.Draw(sheet)
    d.rectangle([0, 0, W, HEAD], fill=(30, 30, 32))
    d.text((20, 15), f"{build.name} close-ups", font=title, fill=(255, 170, 60))
    d.text((W - 520, 22), f"{cfg['time']}   git {cfg['git']}", font=cap, fill=(200, 200, 200))
    for i, c in enumerate(cus):
        for row, (tail, what) in enumerate((("-cam", "with camera"), ("", "camera hidden"))):
            x, y = i * CELL, HEAD + row * CELL
            im = Image.open(build / "render" / f"close-{c['name']}{tail}.png").convert("RGB")
            im.thumbnail((CELL, CELL))
            sheet.paste(im, (x + (CELL - im.width) // 2, y + (CELL - im.height) // 2))
            label = f"{c['label']}, {what}, {c['width']:g} mm across"
            d.text((x + 12, y + 8), label, font=cap, fill=(20, 20, 20))
    out = build / "closeups.png"
    sheet.save(out)
    print(f"sheet: {out}")


if __name__ == "__main__":
    main()
