"""Write one build folder: printable meshes, the assembly, sizes, checks, config and the print
list."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from build123d import Color, Compound, Location, Mesher, export_gltf, export_step, export_stl

from .checks import oriented, volume
from .design import Design

PLA_DENSITY = 1.24  # g/cm3, solid


def next_dir(root: Path, design: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    pat = re.compile(rf"^{re.escape(design)}-(\d+)$")
    nums = [int(m.group(1)) for p in root.iterdir() if (m := pat.match(p.name))]
    n = max(nums) + 1 if nums else 1
    while True:  # builds running side by side take the next free number
        out = root / f"{design}-{n:02d}"
        try:
            out.mkdir()
            return out
        except FileExistsError:
            n += 1


def _bb(s):
    bb = s.bounding_box()
    lo = [round(bb.min.X, 2), round(bb.min.Y, 2), round(bb.min.Z, 2)]
    hi = [round(bb.max.X, 2), round(bb.max.Y, 2), round(bb.max.Z, 2)]
    return {"min": lo, "max": hi, "size": [round(hi[i] - lo[i], 2) for i in range(3)]}


def _matrix(loc: Location) -> list:
    """4x4 rows of a Location, mm."""
    t = loc.wrapped.Transformation()
    rows = [[round(t.Value(i, j), 9) for j in range(1, 5)] for i in range(1, 4)]
    return [*rows, [0.0, 0.0, 0.0, 1.0]]


def _git(case: Path) -> str:
    if os.environ.get("C100_GIT"):  # a copy of the tree built elsewhere
        return os.environ["C100_GIT"]
    try:
        sha = subprocess.run(
            ["git", "-C", str(case), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(case), "status", "--porcelain", "--", "."],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def assembly(d: Design, exploded: bool = False) -> Compound:
    kids = []
    for b in d.bodies:
        s = b.solid.moved(Location(tuple(b.explode) if exploded else (0, 0, 0)))
        s.label, s.color = b.name, Color(*b.color)
        kids.append(s)
    for p in d.parts:
        s = p.shape.moved(Location(tuple(p.explode) if exploded else (0, 0, 0)))
        s.label = p.name
        kids.append(s)
    return Compound(children=kids, label=d.name)


def _entry(db, pid: str) -> dict:
    for _name, _path, data in db.sources:
        if pid in data:
            return data[pid]
    return {}


def _link(src) -> str:
    for s in src if isinstance(src, list) else [src]:
        if isinstance(s, str) and s.startswith("http"):
            return s.split(" ")[0]
    return ""


def print_list(d: Design, cfg, sizes: dict, lines: list, db, build: str) -> str:
    """print.md: bodies with file, PLA colour, orientation and supports; hardware; parts to
    buy, from the parts data."""
    keys = [f"screen {cfg.screen}"] if hasattr(cfg, "screen") else []
    failed = sum(1 for ln in lines if not ln["pass"])
    md = [
        f"# {build} print list",
        "",
        f"{d.name}{', ' + ', '.join(keys) if keys else ''}. PLA, 0.4 mm nozzle. "
        + ("Checks pass." if not failed else f"{failed} checks FAIL: see checks.json.")
        + " The STL and 3MF files are already turned for printing.",
        "",
        "| body | file | PLA | as printed, mm | orientation | supports |",
        "|---|---|---|---|---|---|",
    ]
    over = {ln["name"]: ln["detail"] for ln in lines if ln["check"] == "overhang"}
    for b in d.bodies:
        size = " x ".join(f"{v:.1f}" for v in sizes["bodies"][b.name]["as_printed"])
        note = b.note.removeprefix(f"{b.pla} PLA, ") if b.pla else b.note
        down = over.get(f"{b.name} faces facing down past 45 deg", "")
        sup = "none planned" + (f"; {down} face down past 45 deg" if down != "0 mm2" else "")
        md.append(f"| {b.name} | {b.name}.3mf, .stl | {b.pla or 'any'} | {size} | {note} | {sup} |")
    md += ["", "Hardware:", ""]
    md += [f"- {h['qty']}x {h['item']}: {h['use']}" for h in d.hardware]
    buy = {}
    for p in d.parts:
        if p.role == "camera":
            continue
        for pid in getattr(p.shape, "pids", None) or [getattr(p.shape, "pid", None)]:
            if pid is None:
                continue
            e = _entry(db, pid)
            row = buy.setdefault(
                pid, [e.get("name", pid), 0, e.get("price_usd"), _link(e.get("src"))]
            )
            row[1] += 1
    md += ["", "Parts to buy:", "", "| part | qty | price | link |", "|---|---|---|---|"]
    for name, qty, price, link in buy.values():
        md.append(f"| {name} | {qty} | {f'${price:.2f}' if price else '-'} | {link or '-'} |")
    md += ["", "Wiring: README.md."]
    return "\n".join(md) + "\n"


def write(
    d: Design, cfg, overrides, parts_report, lines, out: Path, case: Path, argv: list[str], db=None
):
    sizes = {"design": d.name, "build": out.name, "bodies": {}, "parts": {}}
    for b in d.bodies:
        s = oriented(b)
        s.label, s.color = b.name, Color(*b.color)
        export_stl(s, out / f"{b.name}.stl", tolerance=0.01, angular_tolerance=0.1)
        m = Mesher()
        m.add_shape(s, linear_deflection=0.01, angular_deflection=0.1, part_number=b.name)
        m.write(str(out / f"{b.name}.3mf"))
        v = volume(b.solid) / 1000.0
        sizes["bodies"][b.name] = {
            "assembled": _bb(b.solid),
            "as_printed": _bb(s)["size"],
            "print_rotation_deg": list(b.print_rot),
            "volume_cm3": round(v, 2),
            "mass_g_solid_pla": round(v * PLA_DENSITY, 1),
            "note": b.note,
        }
    for p in d.parts:
        sizes["parts"][p.name] = {
            "bbox": _bb(p.shape),
            "estimate": bool(getattr(p.shape, "estimate", True)),
            "src": getattr(p.shape, "src", []),
        }
    case_bb = Compound(children=[b.solid.moved(Location((0, 0, 0))) for b in d.bodies])
    sizes["case_overall"] = _bb(case_bb)
    asm = assembly(d)
    sizes["assembly_overall"] = _bb(asm)
    sizes["hardware"] = d.hardware
    sizes["notes"] = d.notes
    export_step(asm, out / "assembly.step")
    export_gltf(
        asm, out / "assembly.glb", binary=True, linear_deflection=0.05, angular_deflection=0.2
    )
    render = {
        "design": d.name,
        "build": out.name,
        "objects": [
            {"name": b.name, "role": "body", "color": list(b.color), "explode": list(b.explode)}
            for b in d.bodies
        ]
        + [{"name": p.name, "role": p.role, "explode": list(p.explode)} for p in d.parts],
        "poses": [
            {"name": name, "moves": {k: _matrix(v) for k, v in moves.items()}, "hide": list(hide)}
            for name, moves, hide in d.poses
        ],
        "closeups": [
            {
                "name": c.name,
                "label": c.label or c.name,
                "target": list(c.target),
                "direction": list(c.direction),
                "width": c.width,
                "moves": {k: _matrix(v) for k, v in c.moves.items()},
            }
            for c in d.closeups
        ],
    }
    failed = [ln for ln in lines if not ln["pass"]]
    (out / "render.json").write_text(json.dumps(render, indent=1) + "\n")
    (out / "sizes.json").write_text(json.dumps(sizes, indent=1) + "\n")
    (out / "checks.json").write_text(
        json.dumps({"pass": not failed, "failed": len(failed), "lines": lines}, indent=1) + "\n"
    )
    config = {
        "design": d.name,
        "build": out.name,
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git": _git(case),
        "argv": argv,
        "overrides": overrides,
        "values": dataclasses.asdict(cfg),
        "parts": parts_report,
    }
    (out / "config.json").write_text(json.dumps(config, indent=1) + "\n")
    if db is not None:
        (out / "print.md").write_text(print_list(d, cfg, sizes, lines, db, out.name))
    return sizes
