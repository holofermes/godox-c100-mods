"""Bought parts as simple solids in their own frames (see each part's `frame` in the parts files).

Each builder returns a Compound of coloured children; designs place it with a Location. Builders
attach what designs and checks need: `pid`, `mass_g`, `estimate`, `src` and part dimensions.
"""

from __future__ import annotations

from build123d import Circle, Compound, Cylinder, Location, Pos

from .geom import along_x, along_y, box, cyl, rbox_z, rrect
from .parts import PartsDB

PCB_GREEN = (0.10, 0.42, 0.22)
PCB_BLACK = (0.10, 0.10, 0.11)
METAL = (0.75, 0.76, 0.78)
DARK = (0.12, 0.12, 0.13)
GOLD = (0.85, 0.68, 0.25)
BATTERY = (0.30, 0.45, 0.70)
SCREEN = (0.05, 0.10, 0.25)
TAN = (0.80, 0.72, 0.55)
KEEPOUT = (0.95, 0.85, 0.30)
CAP_BLUE = (0.15, 0.20, 0.45)
SMOKE = (0.25, 0.25, 0.27)
WHITE = (0.92, 0.91, 0.88)

META = (
    "pid",
    "pids",
    "mass_g",
    "estimate",
    "src",
    "psize",
    "holes",
    "dims",
    "t_max",
    "top",
    "bottom",
    "card_out",
    "pad_d",
    "extent",
)


def _c(shape, label, color):
    shape.label = label
    shape.color = color
    return shape


def _bx(d: dict, label: str, color):
    return _c(box(d["x0"], d["x1"], d["y0"], d["y1"], d["z0"], d["z1"]), label, color)


def _src(spec) -> list:
    s = spec.get("_src", [])
    return list(s) if isinstance(s, list) else [s]


def _comp(label, children, spec, db: PartsDB, pid: str):
    c = Compound(children=children, label=label)
    c.pid = pid
    c.pids = [pid]  # every bought part in it
    c.mass_g, mest, _ = db.mass(pid)
    c.estimate = bool(spec.get("_estimate", True)) or mest
    c.src = _src(spec)
    return c


def pi_zero_w(db: PartsDB, room: float | None = None) -> Compound:
    """Pi Zero W in its board frame (corner origin, parts.json), headerless: wires soldered into
    a few pins. Its keep-out covers the whole board: its tallest part plus the wire joints.

    room: the keep-out this deep from the underside instead (the ports past it are drawn); at
    least `low`: the tallest part off the port edge or the wire joints, whichever stands higher
    (the joints sit at the GPIO holes, over no part).
    """
    s = db.get(
        "pi_zero_w",
        ("board", "holes", "mini_hdmi", "usb_data", "usb_power", "microsd", "csi"),
    )
    f = s["features"]
    bd = f["board"]
    L, Wd, t = bd["x"], bd["y"], bd["t"]
    pcb = rbox_z(0, L, 0, Wd, 0, t, bd.get("corner_r", 3.0))
    for x, y in f["holes"]["points"]:
        pcb -= Pos(x, y, t / 2) * Cylinder(f["holes"]["d"] / 2, t + 1)
    kids = [_c(pcb, "pi_pcb", PCB_GREEN)]
    t_max = max(bd.get("t_range", [t]))
    hl = db.get("pi_zero_w", ("headerless",))["features"]["headerless"]
    tallest = max(f[n]["z1"] for n in ("mini_hdmi", "usb_data", "usb_power", "csi")) - t
    top = t_max + tallest + hl["joint_h"]
    off_edge = max(f["csi"]["z1"], f["microsd"]["socket"]["z1"]) - t
    low = t_max + max(off_edge, hl["joint_h"])
    if room is not None:
        if room < low - 1e-6:
            raise SystemExit(
                f"pi_zero_w: {room:.2f} over the underside, needs {low:.2f} (its parts or joints)"
            )
        top = room
    kids.append(_c(rbox_z(0, L, 0, Wd, t, top, bd.get("corner_r", 3.0)), "pi_keepout", KEEPOUT))
    for name in ("mini_hdmi", "usb_data", "usb_power", "csi"):
        kids.append(_bx(f[name], f"pi_{name}", TAN if name == "csi" else METAL))
    kids.append(_bx(f["microsd"]["socket"], "pi_microsd_socket", METAL))
    kids.append(_bx(f["microsd"]["card"], "pi_microsd_card", DARK))
    comp = _comp("pi_zero_w", kids, s, db, "pi_zero_w")
    comp.estimate = True  # the headerless keep-out is an estimate
    bb = comp.bounding_box()
    comp.extent = ((bb.min.X, bb.min.Y, bb.min.Z), (bb.max.X, bb.max.Y, bb.max.Z))
    comp.holes = [tuple(p) for p in f["holes"]["points"]]
    comp.psize = (L, Wd, t)
    comp.t_max = t_max
    comp.corner_r = bd.get("corner_r", 3.0)
    comp.top = top
    comp.low = low
    comp.bottom = 0.0
    comp.card_out = -f["microsd"]["card"]["x0"]
    comp.pad_d = f["holes"].get("pad_d", 6.0)
    return comp


def lipo(db: PartsDB, pid: str) -> Compound:
    """Pouch cell: X the lead end, Y length, Z thickness; leads leave the +Y end."""
    s = db.get(pid, ("leads",))
    X, Y, Z = s["size"]
    ld = s["features"]["leads"]
    cell = rbox_z(-X / 2, X / 2, -Y / 2, Y / 2, 0, Z, 1.5)
    lead = box(
        -ld["w"] / 2,
        ld["w"] / 2,
        Y / 2,
        Y / 2 + ld["len"],
        Z / 2 - ld["t"] / 2,
        Z / 2 + ld["t"] / 2,
    )
    kids = [_c(cell, "battery_cell", BATTERY), _c(lead, "battery_leads", DARK)]
    comp = _comp("battery", kids, s, db, pid)
    comp.psize = (X, Y, Z)
    comp.dims = {"lead_len": ld["len"], "lead_w": ld["w"]}
    return comp


def usb_c_plug(db: PartsDB, pid: str) -> Compound:
    """USB-C plug board in its plug frame: origin on the axis at the shoulder, +Z along the plug."""
    s = db.get(pid, ("mating_len", "shell", "rear", "board", "holes"))
    f = s["features"]
    sh = f["shell"]
    r = (sh["y1"] - sh["y0"]) / 2 - 0.05
    shell = rbox_z(sh["x0"], sh["x1"], sh["y0"], sh["y1"], sh["z0"], sh["z1"], r)
    kids = [
        _c(shell, "plug_shell", METAL),
        _bx(f["rear"], "plug_rear", METAL),
        _bx(f["board"], "plug_board", PCB_BLACK),
    ]
    comp = _comp("usb_c_plug", kids, s, db, pid)
    comp.dims = {k: f[k] for k in ("shell", "rear", "board", "holes", "mating_len")}
    bb = comp.bounding_box()
    comp.extent = ((bb.min.X, bb.min.Y, bb.min.Z), (bb.max.X, bb.max.Y, bb.max.Z))
    return comp


def capacitor(db: PartsDB, pid: str, label: str) -> Compound:
    """Radial electrolytic, axis along Z, leads at Z = 0."""
    s = db.get(pid)
    d, _, h = s["size"]
    can = Pos(0, 0, h / 2) * Cylinder(d / 2, h)
    comp = _comp(label, [_c(can, f"{label}_can", CAP_BLUE)], s, db, pid)
    comp.psize = (d, d, h)
    return comp


def screen_module(db: PartsDB, pid: str, plug: dict | None = None, cable: dict | None = None):
    """A screen module kept whole in its housing (argon_oled_v5), in the screen frame: back
    plate, front frame (the push button), display, the socket on its +Z edge with the plug
    mated and the cable bent 90 degrees toward +Y (into whatever the module is mounted on).
    `plug`, `cable`: sizes that replace the part's (a design's measured or generous ones)."""
    s = db.get(pid, ("plate", "frame", "display", "holes", "conn", "plug", "cable"))
    X, Z = s["size"][0], s["size"][1]
    f = dict(s["features"])
    f["plug"] = {**f["plug"], **(plug or {})}
    f["cable"] = {**f["cable"], **(cable or {})}
    pt, ft, d = f["plate"]["t"], f["frame"]["t"], f["display"]
    r = f["plate"]["r"]
    t = ft + pt
    plate = along_y(rrect(X, Z, r), ft, t)
    frame = along_y(rrect(X, Z, r), 0.0, ft - 0.01)
    for x, z in f["holes"]["points_xz"]:
        hole = cyl(f["holes"]["d"] / 2, (x, -1, z), (x, t + 1, z))
        plate -= hole
        frame -= cyl(f["holes"]["d"] / 2 + 1.5, (x, -1, z), (x, t + 1, z))
    v = d.get("v", 0.0)
    disp = box(-d["w"] / 2, d["w"] / 2, -d["proud"], 0.01, v - d["h"] / 2, v + d["h"] / 2)
    cn, pg, cb = f["conn"], f["plug"], f["cable"]
    yc = (cn["y0"] + cn["y1"]) / 2
    z_edge = Z / 2
    socket = box(-cn["w"] / 2, cn["w"] / 2, cn["y0"], cn["y1"], z_edge - cn["depth"], z_edge + 0.01)
    z_end = z_edge + pg["out"]
    plug = box(-pg["w"] / 2, pg["w"] / 2, yc - pg["t"] / 2, yc + pg["t"] / 2, z_edge, z_end)
    # wires: up out of the plug, a quarter bend toward +Y, then straight on
    rb, ct, cw = cb["bend_r"], cb["t"], cb["w"]
    ring = along_x(Circle(rb + ct / 2) - Circle(rb - ct / 2), -cw / 2, cw / 2)
    ring = Pos(0, yc + rb, z_end) * ring
    bend = ring & box(-cw, cw, yc - ct, yc + rb, z_end, z_end + rb + ct)
    run = box(
        -cw / 2, cw / 2, yc + rb, yc + rb + cb["run"], z_end + rb - ct / 2, z_end + rb + ct / 2
    )
    kids = [
        _c(plate, "screen_plate", SMOKE),
        _c(frame, "screen_frame", SMOKE),
        _c(disp, "screen_display", SCREEN),
        _c(socket, "screen_socket", WHITE),
        _c(plug, "screen_plug", WHITE),
        _c(bend + run, "screen_cable", DARK),
    ]
    comp = _comp("screen", kids, s, db, pid)
    comp.dims = {
        "w": X,
        "h": Z,
        "t": t,
        "r": r,
        "plate_t": pt,
        "frame_t": ft,
        "display": d,
        "holes": f["holes"],
        "conn": cn,
        "plug": pg,
        "cable": cb,
        "plug_y": yc,
        "plug_end": z_end,
    }
    return comp


# power board features drawn as boxes, in this order, with their colours
POWER_BOXES = (
    ("parts_low", "power_parts", DARK),
    ("usb_c", "power_usb_c", METAL),
    ("button", "power_button", TAN),
    ("jst", "power_jst", TAN),
    ("switch", "power_switch", METAL),
    ("lever", "power_lever", DARK),
    ("inductor", "power_inductor", SMOKE),
    ("leds", "power_leds", GOLD),
    ("out_5v", "power_out_5v", GOLD),
)


def power_board(db: PartsDB, pid: str) -> Compound:
    """Charge/boost board in its board frame (the Eagle file's), Z up from the PCB underside,
    parts on top as boxes."""
    base = db.get(pid)["features"]
    names = ["pcb"] + [n for n in ("parts_side", "holes") if n in base]
    names += [n for n, _, _ in POWER_BOXES if "z0" in base.get(n, {})]
    s = db.get(pid, tuple(names))
    f = s["features"]
    p = f["pcb"]
    if "r" in p:
        pcb = rbox_z(p["x0"], p["x1"], p["y0"], p["y1"], p["z0"], p["z1"], p["r"])
    else:
        pcb = box(p["x0"], p["x1"], p["y0"], p["y1"], p["z0"], p["z1"])
    for x, y in f.get("holes", {}).get("points_xy", []):
        pcb -= cyl(f["holes"]["d"] / 2, (x, y, p["z0"] - 1), (x, y, p["z1"] + 1))
    kids = [_c(pcb, "power_pcb", tuple(p.get("rgb", PCB_BLACK)))]
    if "parts_side" in f:
        kids.append(_bx({**p, "z0": p["z1"], "z1": f["parts_side"]["z1"]}, "power_parts", DARK))
    for n, label, col in POWER_BOXES:
        if n not in names:
            continue
        v = f[n]
        if n == "usb_c" and v.get("opening") == "-x":
            # a USB-C receptacle's shell has round ends: across the board, its height round
            dy, dz = v["y1"] - v["y0"], v["z1"] - v["z0"]
            cy, cz = (v["y0"] + v["y1"]) / 2, (v["z0"] + v["z1"]) / 2
            kids.append(_c(along_x(rrect(dy, dz, dz / 2, cy, cz), v["x0"], v["x1"]), label, col))
        else:
            kids.append(_bx(v, label, col))
    comp = _comp("power_board", kids, s, db, pid)
    comp.psize = tuple(s["size"])
    comp.dims = {k: f[k] for k in names}
    bb = comp.bounding_box()
    comp.extent = ((bb.min.X, bb.min.Y, bb.min.Z), (bb.max.X, bb.max.Y, bb.max.Z))
    return comp


def place(comp: Compound, loc: Location, label: str | None = None) -> Compound:
    """Move a part; keeps its label and the metadata the builders attach."""
    out = loc * comp
    for k in META:
        if k in vars(comp):
            setattr(out, k, vars(comp)[k])
    out.label = label or comp.label
    return out
