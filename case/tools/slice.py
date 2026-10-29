"""Slice printed bodies for the Bambu Lab X1C with OrcaSlicer's command line, supports per body.

uv run tools/slice.py --name NAME --out DIR STL:SUPPORT [STL:SUPPORT ...]
    e.g. out/case-NN/back.stl:plate out/case-NN/front.stl:none
    SUPPORT: tree (tree supports wherever needed), plate (tree supports from the plate only,
    nothing inside the body), none.

Needs ORCA_SLICER, the OrcaSlicer AppImage, and Orca's system presets in
~/.config/OrcaSlicer/system/BBL (Orca writes them on its first start).

Writes DIR/NAME.3mf, the project to open in Orca (Slice plate, then Print), and
DIR/NAME.gcode.3mf, the same plate already sliced here, then prints time, filament and the
support filament per body. STLs go on the plate as they are: export them turned for printing.

The presets are flattened first: the command line does not follow a system preset's
`inherits` chain, and without the full presets it slices at default speeds. Supports are set
on each object: in a project made this way, settings on the objects are kept and the process
setting is not.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SYSTEM = Path.home() / ".config/OrcaSlicer/system/BBL"
SUPPORT = {
    "tree": {"enable_support": "1", "support_type": "tree(auto)"},
    "plate": {
        "enable_support": "1",
        "support_type": "tree(auto)",
        "support_on_build_plate_only": "1",
    },
    "none": {"enable_support": "0"},
}


def flatten(kind: str, name: str, over: dict, out: Path) -> Path:
    """A system preset with its inherits chain resolved, as one JSON file."""

    def load(n: str) -> dict:
        p = SYSTEM / kind / f"{n}.json"
        if not p.is_file():
            sys.exit(f"slice: no {kind} preset {p}")
        d = json.loads(p.read_text())
        parent = d.pop("inherits", "")
        if parent:
            base = load(parent)
            base.update(d)
            d = base
        return d

    d = load(name)
    d.update(over)
    d["name"] = name
    d["from"] = "system"
    d.pop("instantiation", None)
    out.write_text(json.dumps(d, indent=1))
    return out


def orca(args: list[str], log: Path) -> None:
    exe = os.environ.get("ORCA_SLICER")
    if not exe or not Path(exe).is_file():
        sys.exit("slice: set ORCA_SLICER to the OrcaSlicer AppImage")
    env = {**os.environ, "APPIMAGE_EXTRACT_AND_RUN": "1"}
    with log.open("w") as f:
        r = subprocess.run([exe, *args], stdout=f, stderr=subprocess.STDOUT, env=env)
    if r.returncode != 0:
        sys.exit(f"slice: OrcaSlicer exited {r.returncode}, see {log}")


def with_supports(src: Path, dst: Path, per: dict[str, dict]) -> None:
    """Copy a project 3MF, adding settings to each object named in `per`."""
    with (
        zipfile.ZipFile(src) as zin,
        zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "Metadata/model_settings.config":
                text = data.decode()
                for name, settings in per.items():
                    pat = re.compile(
                        r'(<metadata key="name" value="' + re.escape(name) + r'"/>\s*'
                        r'<metadata key="extruder" value="\d+"/>)'
                    )
                    if len(pat.findall(text)) != 1:
                        sys.exit(f"slice: object {name} not found once in the project")
                    lines = "".join(
                        f'\n    <metadata key="{k}" value="{v}"/>' for k, v in settings.items()
                    )
                    text = pat.sub(lambda m, lines=lines: m.group(1) + lines, text)
                data = text.encode()
            zout.writestr(item, data)


def report(sliced: Path) -> None:
    with zipfile.ZipFile(sliced) as z:
        info = z.read("Metadata/slice_info.config").decode()
        gcode = z.read("Metadata/plate_1.gcode").decode().splitlines()
    names = dict(re.findall(r'<object identify_id="(\d+)" name="([^"]+)"', info))
    for line in gcode[:400]:
        if line.startswith(("; model printing time", "; total layer number")):
            print(line[2:])
        if line.startswith(("; total filament weight", "; curr_bed_type", "; outer_wall_speed")):
            print(line[2:])
    support: dict[tuple[str, bool], float] = {}
    obj, z, feat = None, 0.0, ""
    for line in gcode:
        if line.startswith("; start printing object, unique label id:"):
            obj = line.split(":")[-1].strip()
        elif line.startswith("; stop printing object"):
            obj = None
        elif line.startswith(("; Z_HEIGHT:", ";Z:")):
            z = float(line.split(":")[1])
        elif line.startswith("; FEATURE:"):
            feat = line.split(":", 1)[1].strip().lower()
        elif obj and feat.startswith("support") and line.startswith("G1") and " E" in line:
            e = float(re.search(r"E(-?[\d.]+)", line).group(1))
            if e > 0:
                key = (obj, z > 1.0)
                support[key] = support.get(key, 0.0) + e
    for oid, name in sorted(names.items(), key=lambda x: x[1]):
        low, high = support.get((oid, False), 0.0), support.get((oid, True), 0.0)
        print(f"support {name}: {low:.0f} mm of filament in the first 1 mm, {high:.0f} above")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bodies", nargs="+", metavar="STL:SUPPORT")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--machine", default="Bambu Lab X1 Carbon 0.4 nozzle")
    ap.add_argument("--process", default="0.20mm Standard @BBL X1C")
    ap.add_argument("--filament", default="Bambu PLA Basic @BBL X1C")
    ap.add_argument("--plate", default="Textured PEI Plate")
    a = ap.parse_args()

    work = a.out / f"{a.name}-work"
    if work.exists():
        shutil.rmtree(work)
    (work / "sliced").mkdir(parents=True)
    stls, per = [], {}
    for spec in a.bodies:
        path, _, kind = spec.rpartition(":")
        if kind not in SUPPORT or not Path(path).is_file():
            sys.exit(f"slice: want STL:SUPPORT with SUPPORT in {sorted(SUPPORT)}, got {spec}")
        dst = work / f"{a.name}-{Path(path).stem}.stl"
        if dst.exists():
            sys.exit(f"slice: two bodies named {Path(path).stem}")
        shutil.copy2(path, dst)
        stls.append(str(dst))
        per[dst.name] = SUPPORT[kind]

    over = {
        "enable_support": "1",
        "support_type": "tree(auto)",
        "curr_bed_type": a.plate,
    }
    m = flatten("machine", a.machine, {}, work / "machine.json")
    p = flatten("process", a.process, over, work / "process.json")
    f = flatten("filament", a.filament, {}, work / "filament.json")
    orca(
        [
            "--arrange", "1", "--orient", "0",
            "--load-settings", f"{m};{p}", "--load-filaments", str(f),
            "--outputdir", str(work), "--export-3mf", "plain.3mf", *stls,
        ],
        work / "export.log",
    )  # fmt: skip
    project = a.out / f"{a.name}.3mf"
    with_supports(work / "plain.3mf", project, per)
    orca(
        [
            "--slice", "0", "--outputdir", str(work / "sliced"),
            "--export-3mf", f"{a.name}.gcode.3mf", str(project),
        ],
        work / "slice.log",
    )  # fmt: skip
    result = json.loads((work / "sliced/result.json").read_text())
    if result.get("error_string") != "Success.":
        sys.exit(f"slice: {result.get('error_string')}, see {work / 'slice.log'}")
    for plate in result.get("sliced_plates", []):
        if plate.get("warning_message"):
            print(f"warning: {plate['warning_message']}")
    sliced = a.out / f"{a.name}.gcode.3mf"
    shutil.copy2(work / "sliced" / sliced.name, sliced)
    print(f"slice: {project} (open in Orca) and {sliced}")
    report(sliced)


if __name__ == "__main__":
    main()
