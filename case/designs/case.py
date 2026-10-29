"""The case: two printed halves, split front to back, each wired open on the bench.

The skin is the camera's clean lower body grown and continued down; the camera sits in a cup cut
from its top, the USB-C plug board in the cup's floor going into its port.

Inside, back to front: the headerless Zero with its top toward the back, the wires' gap, the
power board with its flat side toward the Zero and its parts toward the front, the 1200 mAh
cell. The case splits through the middle of the wires' gap into two printed bodies:
- back: the screen module on the back face, the plug board hanging under the cup, the Zero. Its
  wires (screen to GPIO, the plug's USB data to the Zero) stay in this half.
- front: the power board and the cell, wired to each other in this half.
Four wires cross between the halves, 5 V and ground to the plug board and to the Zero.

Each half prints lying on its outer face, so every holder stands off that face along Y: the
Zero lies top side down on a column at each mounting hole, a post under its port edge near
each end and a fin past its +X end; the power board sits with its receptacle in the charge port
and a block in front of it, its bottom edge (below the cell) on two ledges with a lip under it,
the +X one on past its end; the cell in a rib at each end over a foot under it, its lead at -X.

The halves meet on a lap: a tongue from the back half `lap_h` into the front half inside its
walls, `lap_gap` from them, round the sides, the bottom and under the cup, on a band that
thickens the back half's walls under it. They close with four M2.5 screws from the back face
into heat-set inserts in bosses on the front half: two through columns and the Zero's lower
mounting holes (the Zero clamped between column and boss), two at the top corners.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from build123d import (
    Face,
    GeomType,
    Location,
    Plane,
    Solid,
    Vector,
    Vertex,
    extrude,
    fillet,
)
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.collections import Array1_double, Array1_int, Array2_gp_Pnt
from OCP.Geom import Geom_BSplineSurface
from OCP.gp import gp_Pnt

from lib import checks, models
from lib.camera import Camera
from lib.config import Common
from lib.design import Body, Closeup, Design, Hole, Placed
from lib.geom import (
    along_x,
    along_y,
    along_z,
    bbox,
    box,
    cyl,
    face_at,
    frame_loc,
    in_rounded_block,
    poly_rounded,
    rect4,
    rounded_block,
    rrect,
)
from lib.parts import PartsDB

BACK_RGB = (0.80, 0.70, 0.58)
FRONT_RGB = (0.62, 0.55, 0.48)
PARTS = ("battery", "power_board", "screen", "pi_zero_w")  # the 470 uF rides on the plug board
LIP_KEEPOUTS = ("shutter", "pill", "mode_switch", "mic", "lanyard")
SURFACE_MARGINS = (1.0, 1.7, 1.3, 2.1)  # lip surface past the skin, tried in turn


@dataclass
class Config(Common):
    wall: float = 1.2  # walls
    floor: float = 1.2  # the cup's floor under the camera
    lip: str = "wrap"  # flat: lip_h all round; wrap: wrap_* heights round the perimeter
    lip_h: float = 5.0  # flat: lip top over the camera's bottom; at most 5.4 (shutter room)
    wrap_side: float = 12.2  # wrap: on both sides; the +X mode switch finger room from 12.7
    wrap_front: float = 17.6  # wrap: along the front; the -X mic room from 18.1
    wrap_back: float = 4.9  # wrap: bottom of the back dip; the shutter finger room from 5.4
    wrap_dip: float = 36.0  # wrap: width of the back dip's bottom, centred on the shutter
    wrap_ramp: float = 15.0  # wrap: each smooth rise from the dip to the side height
    wrap_margin: float = 0.5  # wrap: the top raised this much still clears every keep-out
    lanyard: bool = True  # wrap: a dip in the -X lip at the camera's lanyard holes
    lanyard_w: float = 7.0  # its flat bottom along Y, centred on the holes
    lanyard_h: float = 5.5  # its bottom over the camera's bottom
    lanyard_ramp: float = 4.0  # each smooth rise from it
    lanyard_cord: float = 2.0  # cord room round the holes, out and down (the keep-out)
    lip_r: float = 0.5  # round on both top edges of the lip, under half the lip wall
    lip_wall: float = 0.0  # 0: as thin as the stack's depth allows, at least wall + lip_r
    lap: bool = True  # lap joint round the cut
    lap_h: float = 3.0  # the back half's tongue into the front half, inside its walls
    lap_gap: float = 0.2  # slip fit between the front half's walls and the tongue
    lap_tongue: float = 0.0  # tongue thickness; 0: min_wall
    head_d: float = 5.0  # counterbore for the socket heads (4.5, ISO 4762)
    head_h: float = 2.6  # head 2.5
    insert_d: float = 3.86  # McMaster 94180A321 (plug board, screen module): max hole
    insert_len: float = 3.4  # installed length
    insert_min: float = 4.17  # least plastic it needs
    insert_pn: str = "McMaster 94180A321"
    insert_wall: float = 1.6  # plastic round an insert's hole
    screw_d: float = 2.9  # clearance for an M2.5
    kit: str = "4,6,8,10,12,16,20,25"  # M2.5 socket head screw lengths to pick from
    battery: str = "lipo_1200"
    power: str = "lipo_rider_plus"
    plug: str = "usb_c_plug_ada5978"
    capacitor: str = "cap_470u_63"
    screen: str = "argon_oled_v5"  # the module on the back face
    screen_x: float = 0.0  # module centre X wanted (the middle); it moves the least its posts
    # need to clear the power board's parts
    screen_top: float = 14.01  # module top edge under the camera
    screen_gap: float = 0.4  # even gap round the module's outline in its pocket
    screen_seat: float = 0.2  # pocket deeper than the module's back plate
    screen_plug_w: float = 9.11  # the cable's plug housing (JST ZH 5-pin), measured
    screen_plug_t: float = 3.38  # measured
    screen_wire_w: float = 5.82  # the five wires side by side, measured
    screen_notch_w: float = 9.5  # the wires' notch in the back wall
    screen_notch_h: float = 2.05  # up from the seam: the wires' highest bend, measured
    block_margin: float = 1.0  # plug block past the plug board each side
    holder_w: float = 2.4  # posts, fins and ribs that hold the Zero and the cell
    holder_foot: float = 3.0  # each cell rib's foot under the cell's end
    zero_pcb: float = 0.0  # the Zero's PCB thickness, measured; 0: parts.json's
    zero_end_gap: float = 1.0  # the Zero's end fin stands this much more off its -X end, clear
    # of its camera connector
    plug_body_w: float = 12.35  # charge cable overmold kept clear outside the port (spec max)
    plug_body_h: float = 6.5
    plug_body_len: float = 20.0  # how far out of the case the overmold keep-out reaches
    explode: float = 1.0
    wire_gap: float = 0.0  # Zero underside to power board flat side; 0: the joints on both
    # (twice the Zero's joint height) plus the clearance
    close_play: float = 0.05  # the Zero's PCB between column and boss gets this much room
    pb_strip: float = 2.5  # the power board's bottom edge this far below the cell (ledges)
    pb_stop: bool = True  # a post from the back half behind the power board's +X top corner
    insert_relief: float = 3.0  # a blind insert's hole goes on this far past it, as wide: room
    # for the plastic it pushes ahead and the screw's tip


def corner_zb(cx, cz, rho, x_flat, R, w):
    """Highest case bottom that keeps a circle (centre cx, cz, radius rho) inside a case whose
    bottom corners have radius R, walls w, flat bottom out to |x| = x_flat."""
    zb = cz - rho - w
    dx = abs(cx) - x_flat
    if dx > 0:
        r_in = R - w - rho
        if r_in >= dx:
            zb = min(zb, cz - R + math.sqrt(r_in**2 - dx**2))
        else:
            zb = min(zb, cz - R)
    return zb


def x_min_at(y_lo, W, rb, w, c):
    """Lowest X a part corner may take, clear of the -X wall and the cavity's back corner round,
    when the part reaches back to y_lo (from the outer back face)."""
    x_wall = -W / 2 + w + c
    r, dy = rb - w - c, rb - y_lo
    if dy <= 0:
        return x_wall
    if dy > r + 1e-9:
        raise SystemExit("case: a power board part reaches into the back wall")
    return max(x_wall, -W / 2 + rb - math.sqrt(max(0.0, r * r - dy * dy)))


def corners(shape):
    bb = shape.bounding_box()
    return [
        (x, y, z)
        for x in (bb.min.X, bb.max.X)
        for y in (bb.min.Y, bb.max.Y)
        for z in (bb.min.Z, bb.max.Z)
    ]


def smooth(t: float) -> float:
    """0 to 1 over t in [0, 1], first and second derivatives 0 at both ends."""
    t = min(1.0, max(0.0, t))
    return t * t * t * (t * (6 * t - 15) + 10)


def kit_screw(cfg, need: float, room: float) -> float:
    """Shortest kit length that reaches `need`, else the longest that fits in `room`."""
    kit = sorted(float(v) for v in cfg.kit.split(","))
    fit = [v for v in kit if v <= room + 1e-9]
    if not fit:
        raise SystemExit(f"case: no kit screw fits in {room:.2f}")
    good = [v for v in fit if v >= need - 1e-9]
    return good[0] if good else fit[-1]


# lip top -----------------------------------------------------------------------------------


def wrap_terms(cfg, cam: Camera, xc: float, y0: float, y1: float, W: float):
    """The lip top height field as a sum of separable terms h(x, y) = sum a(x) b(y): the back
    dip B(x) round x = xc, the rise v(y) to the front height from y0 to y1, and on the -X side
    (S(x), stepping inside the cup) the lanyard dip L(y) blended in. Returns the terms, the
    breaks of each variable and the degree in y."""
    S, K, F = cfg.wrap_side, cfg.wrap_back, cfg.wrap_front
    d, r = cfg.wrap_dip / 2, cfg.wrap_ramp

    def B(x):
        return K + (S - K) * smooth((abs(x - xc) - d) / r)

    def v(y):
        return smooth((y - y0) / (y1 - y0))

    xb = [xc - d - r, xc - d, xc + d, xc + d + r]
    yb = [y0, y1]
    terms = [(B, lambda y: 1 - v(y)), (lambda x: 1.0, lambda y: F * v(y))]
    deg_y = 5
    if cfg.lanyard:
        yl, wl, rl, hl = lanyard_y(cam), cfg.lanyard_w / 2, cfg.lanyard_ramp, cfg.lanyard_h
        a, b = yl - wl - rl, yl + wl + rl
        # the side selector steps from 1 to 0 inside the cup (the dip's Y span is inside it)
        s0, s1 = -W / 2 + 6.0, -W / 2 + 12.0

        def Sx(x):
            return 1 - smooth((x - s0) / (s1 - s0))

        def L(y):
            return smooth((y - a) / rl) * (1 - smooth((y - (b - rl)) / rl))

        def D(y):
            return -(S * (1 - v(y)) + F * v(y) - hl) * L(y)

        terms.append((Sx, D))
        xb += [s0, s1]
        yb += [a, a + rl, b - rl, b]
        deg_y = 10
    return terms, xb, yb, deg_y


def lanyard_y(cam: Camera) -> float:
    holes = cam.f["lanyard_holes"]["holes"]
    return (min(h["u"] - h["w"] / 2 for h in holes) + max(h["u"] + h["w"] / 2 for h in holes)) / 2


def _bernstein(f, a: float, b: float, p: int) -> list[float]:
    """Bezier ordinates of f, a polynomial of degree <= p on [a, b]."""
    ts = [i / p for i in range(p + 1)]
    m = np.array([[math.comb(p, k) * t**k * (1 - t) ** (p - k) for k in range(p + 1)] for t in ts])
    rhs = np.array([f(a + (b - a) * t) for t in ts])
    return list(np.linalg.solve(m, rhs))


def _merged(lo: float, hi: float, breaks) -> list[float]:
    out = [lo]
    for x in sorted(breaks):
        if lo + 1e-6 < x < hi - 1e-6 and x - out[-1] > 1e-6:
            out.append(x)
    return out + [hi]


def lip_surface(terms, xbreaks, ybreaks, deg_y, x0, x1, y0, y1):
    """The height field as one exact B-spline face over the plan box: each term a(x) b(y) is a
    tensor product of Bezier pieces on the merged breaks, degree 5 in x and deg_y in y, its
    poles a_i b_j; joined C2 (knots reduced, exactly); checked against the field."""
    px, py = 5, deg_y
    xs, ys = _merged(x0, x1, xbreaks), _merged(y0, y1, ybreaks)

    def axis(br, p, funcs):
        pos, vals = [], [[] for _ in funcs]
        for k, (a, b) in enumerate(zip(br[:-1], br[1:], strict=True)):
            seg = [a + (b - a) * i / p for i in range(p + 1)]
            pos += seg if k == 0 else seg[1:]
            for j, f in enumerate(funcs):
                o = _bernstein(f, a, b, p)
                vals[j] += o if k == 0 else o[1:]
        return pos, vals

    xpos, avals = axis(xs, px, [t[0] for t in terms])
    ypos, bvals = axis(ys, py, [t[1] for t in terms])
    poles = Array2_gp_Pnt(1, len(xpos), 1, len(ypos))
    for i, x in enumerate(xpos):
        for j, y in enumerate(ypos):
            z = sum(av[i] * bv[j] for av, bv in zip(avals, bvals, strict=True))
            poles.SetValue(i + 1, j + 1, gp_Pnt(x, y, z))

    def arr(vals, kind):
        a = kind(1, len(vals))
        for i, v in enumerate(vals):
            a.SetValue(i + 1, v)
        return a

    srf = Geom_BSplineSurface(
        poles,
        arr(xs, Array1_double),
        arr(ys, Array1_double),
        arr([px + 1] + [px] * (len(xs) - 2) + [px + 1], Array1_int),
        arr([py + 1] + [py] * (len(ys) - 2) + [py + 1], Array1_int),
        px,
        py,
    )
    # the pieces join with C2: down to multiplicity degree - 2, exactly (highest first, the
    # indices of the lower ones stay)
    for i in range(len(xs) - 1, 1, -1):
        if not srf.RemoveUKnot(i, px - 2, 1e-6):
            raise SystemExit("case: the lip surface is not C2 along X")
    for j in range(len(ys) - 1, 1, -1):
        if not srf.RemoveVKnot(j, py - 2, 1e-6):
            raise SystemExit("case: the lip surface is not C2 along Y")

    def h(x, y):
        return sum(a(x) * b(y) for a, b in terms)

    for k in range(96):
        x, y = x0 + (x1 - x0) * ((k * 0.618) % 1), y0 + (y1 - y0) * ((k * 0.382) % 1)
        p = srf.Value(x, y)
        if max(abs(p.X() - x), abs(p.Y() - y), abs(p.Z() - h(x, y))) > 1e-5:
            raise SystemExit("case: the lip surface does not match its height field")
    return Face(BRepBuilderAPI_MakeFace(srf, 1e-7).Face()), h


def solid_over(face, up: float = 60.0):
    """Solid over a face, `up` tall."""
    return extrude(face, amount=up, dir=(0, 0, 1))


def lip_edges(shape, on=None):
    """Edges of the lip top (its spline faces facing up); with `on`, only those lying on
    that solid's surface."""
    out = []
    for f in shape.faces():
        if f.geom_type != GeomType.BSPLINE:
            continue
        if f.normal_at(Vector(*f.vertices()[0].center())).Z < 0.5:  # a point on the face
            continue
        for e in f.edges():
            bb = e.bounding_box()
            if abs(bb.min.X) < 1e-4 and abs(bb.max.X) < 1e-4:
                continue
            if on is not None and checks.distance(Vertex(*e.center()), on) > 1e-3:
                continue
            if not any(e.is_same(o) for o in out):
                out.append(e)
    return out


def try_fillet(shape, edges, r):
    try:
        return fillet(edges, r)
    except ValueError:
        return None


def has_feature(db: PartsDB, pid: str, name: str) -> bool:
    return any(name in ((data.get(pid) or {}).get("features") or {}) for _, _, data in db.sources)


def build(cfg: Config, db: PartsDB) -> Design:
    c, w = cfg.clearance, cfg.wall
    cam = Camera.from_db(db)
    W, Dc = cam.W, cam.b["lower"]["y1"]
    R, er = cam.b["corner_r_xz"], cam.b["edge_r"]
    x_flat = W / 2 - R
    if cfg.lip not in ("flat", "wrap"):
        raise SystemExit(f"case: lip is flat or wrap, not '{cfg.lip}'")
    wrap = cfg.lip == "wrap"
    pnl = cam.f["panel"]
    Df = pnl["y_front"]  # the camera front the skin follows
    port = cam.port()
    px, py = port["x"], port["y"]
    plug = models.usb_c_plug(db, cfg.plug)
    pi = models.pi_zero_w(db)
    bat = models.lipo(db, cfg.battery)
    pb = models.power_board(db, cfg.power)
    cap = models.capacitor(db, cfg.capacitor, "cap_plug")
    if cfg.power != "lipo_rider_plus" or not has_feature(db, cfg.screen, "plate"):
        raise SystemExit("case: power is lipo_rider_plus, screen a module on the back face")
    # the plug out of its socket: the wires' bend (the part's radius and thickness) tops out at
    # the measured height over the seam, which is screen_gap over the module
    cb0 = db.get(cfg.screen, ("cable",))["features"]["cable"]
    plug_out = cfg.screen_gap + cfg.screen_notch_h - cb0["bend_r"] - cb0["t"] / 2
    if plug_out <= 0:
        raise SystemExit(f"case: the wires' bend does not fit under {cfg.screen_notch_h:g}")
    plug_size = {"w": cfg.screen_plug_w, "t": cfg.screen_plug_t, "out": plug_out}
    scr = models.screen_module(db, cfg.screen, plug_size, {"w": cfg.screen_wire_w})
    ko = cam.keepouts(cfg)
    pd = plug.dims
    bd = pd["board"]
    top = max(cfg.wrap_side, cfg.wrap_front, cfg.wrap_back) if wrap else cfg.lip_h

    # lanyard holes on -X: the cord leaves them outward and down over the lip
    lh = cam.f["lanyard_holes"]["holes"]
    yl = lanyard_y(cam)
    ly0 = min(h["u"] - h["w"] / 2 for h in lh) - cfg.lanyard_cord
    ly1 = max(h["u"] + h["w"] / 2 for h in lh) + cfg.lanyard_cord
    lz0 = min(h["v"] - h["h"] / 2 for h in lh) - cfg.lanyard_cord
    lz1 = max(h["v"] + h["h"] / 2 for h in lh) + cfg.lanyard_cord
    ko["lanyard"] = box(-W / 2 - 25, -cam.half_width(yl) + 0.3, ly0, ly1, lz0, lz1)

    # heights under the camera -------------------------------------------------------------
    z_ft = -c  # cup floor, plug shoulder
    z_ceil = z_ft - cfg.floor  # the cup floor's underside
    plug_f = db.get(cfg.plug)["features"]
    wire_room = plug_f["wire_room"]["h"] if "wire_room" in plug_f else 0.0
    z_plug_low = z_ft + bd["z0"] - wire_room  # plug board underside minus its wire joints
    cd, _, ch = cap.psize
    blk = (px + bd["x0"] - cfg.block_margin, px + bd["x1"] + cfg.block_margin)
    cap_x0 = blk[0] - c - ch
    cap_zc = z_ceil - c - cd / 2
    bx, by, bz = bat.psize
    (ex0, ey0, _), (ex1, ey1, _) = pi.extent
    L, Wd, _ = pi.psize
    sw_, sh_ = scr.dims["w"], scr.dims["h"]
    names = ("mini_hdmi", "usb_data", "usb_power")
    conn = db.get("pi_zero_w", names)["features"]
    conn = {n: conn[n] for n in names}
    r_ins = cfg.insert_d / 2 + cfg.insert_wall
    holes_xz = pd["holes"].get("points_xz", [])  # an edge plug board: holes along Y
    z_bt = z_ft + bd["z1"]  # plug board top edge

    # the screen module on the back face, its top edge screen_top under the camera
    sd = scr.dims
    cb = sd["cable"]
    x_mod, z_mt = cfg.screen_x, -cfg.screen_top
    z_mc = z_mt - sh_ / 2
    w_back = w + sd["plate_t"] + cfg.screen_seat

    # Y layers from the skin's back face, back to front: the Zero, the power board, the cell. The skin is the camera grown by g all round: the lip wall plus the clearance,
    # or half the depth the stack needs past the camera. In front of the cell the wall takes
    # the lap's tongue.
    pf = pb.dims
    # the Zero as far toward the cell as its parts off the port edge and its joints let it: the
    # room behind it takes the wires; its keep-out ends on the same plane as without the move
    pi_draw = models.pi_zero_w(db, room=pi.low)
    usb = pf["usb_c"]
    usb_zc = (usb["z0"] + usb["z1"]) / 2
    # the receptacle's opening: its body plus the clearance (along Z, along Y)
    o_w, o_h = usb["y1"] - usb["y0"] + 2 * c, usb["z1"] - usb["z0"] + 2 * c
    boxes = {k: v for k, v in pf.items() if "z1" in v}

    hl = db.get("pi_zero_w", ("headerless",))["features"]["headerless"]
    gap = cfg.wire_gap or 2 * hl["joint_h"] + c  # the joints on both boards' facing sides
    t_pb = pf["pcb"]["z1"] - pf["pcb"]["z0"]
    tall = {k: v for k, v in boxes.items() if k != "pcb"}
    cell_top = z_ceil - c
    cell_zc = cell_top - bx / 2
    lead_len, lead_w = bat.dims["lead_len"], bat.dims["lead_w"]
    lz0, lz1 = cell_zc - lead_w / 2, cell_zc + lead_w / 2
    zero_top = min(z_ceil - c, z_plug_low - c, cap_zc - cd / 2 - c)
    zz = zero_top - ey1  # the Zero's port edge, under the plug and the capacitor
    r_boss = cfg.insert_d / 2 + cfg.insert_wall
    y_hole = min(y for _, y in pi.holes)
    z_pb_min = zz + y_hole + r_boss + c  # the power board's bottom over the screws' bosses

    def cell_spot(Ws, pbx):
        """The cell's X and the power board's height: the cell over the board's lowest parts, as
        near the middle as that allows, its lead at -X; the board's bottom edge pb_strip below
        the cell for its ledges, or lower to clear a tall part under the lead (the board's y
        runs down from its header edge)."""
        x_lo = -Ws / 2 + w + c + by / 2 + lead_len
        x_hi = Ws / 2 - w - c - by / 2
        if x_hi < x_lo:
            raise SystemExit("case: the cell does not fit across the case")
        pbz0 = cell_top - bx - c - cfg.pb_strip + pf["pcb"]["y1"]
        best = None
        n = int((x_hi - x_lo) / 0.1) + 1
        for i in range(n + 1):
            x = min(x_hi, x_lo + 0.1 * i)
            body = (x - by / 2 - c, x + by / 2 + c)
            ld = (x - by / 2 - lead_len - c, x - by / 2)
            opts = [pbz0] + [
                lz0 - c + v["y0"]
                for v in tall.values()
                if pbx + v["x0"] < ld[1] and pbx + v["x1"] > ld[0] and lz0 - c + v["y0"] < pbz0
            ]
            for pbz in opts:
                if pbz - pf["pcb"]["y1"] < z_pb_min - 1e-6:
                    continue
                room = t_pb
                for v in tall.values():
                    xs = (pbx + v["x0"], pbx + v["x1"])
                    zs = (pbz - v["y1"], pbz - v["y0"])
                    on_body = (
                        xs[0] < body[1]
                        and xs[1] > body[0]
                        and zs[0] < cell_top + c
                        and zs[1] > cell_top - bx - c
                    )
                    on_lead = (
                        xs[0] < ld[1] and xs[1] > ld[0] and zs[0] < lz1 + c and zs[1] > lz0 - c
                    )
                    if on_body or on_lead:
                        room = max(room, v["z1"])
                key = (round(room, 3), -round(pbz, 3), abs(x))
                if best is None or key < best[0]:
                    best = (key, {"x": x, "pbz": pbz, "room": room})
        if best is None:
            raise SystemExit("case: no place for the cell over the power board")
        return best[1]

    def layers(g):
        Ws = W + 2 * g
        out = {}
        # Y from the skin's back face, back to front: the Zero, its top toward the back; the
        # wires' gap with the cut through its middle; the power board, flat side toward the
        # Zero; the cell
        conn_back = max(k["z1"] for k in conn.values())  # the Zero's ports, from its underside
        y_zu = max(
            w_back + c + conn_back,  # the ports clear the screen's pad and the back wall
            w_back - w + cfg.insert_min + c + pi.low,  # its keep-out clears the bosses' tips
        )
        y_pb = y_zu + gap  # the power board's flat side
        pbx = -Ws / 2 + w + c - min(v["x0"] for k, v in boxes.items() if k != "usb_c")
        cs = cell_spot(Ws, pbx)
        y_cell = y_pb + cs["room"] + c
        need = y_cell + bz + c + w
        # the cell's +X front corner keeps the clearance to the front vertical edge round on the
        # diagonal (`short` reads the rolling-ball round along X only)
        r_in = er + g - w
        dx = cs["x"] + by / 2 - (Ws / 2 - w - r_in)
        if dx > 0:
            if dx > r_in - c:
                raise SystemExit("case: the cell's +X end is past the front corner round")
            need = max(need, y_cell + bz + w + r_in - math.sqrt((r_in - c) ** 2 - dx * dx))
        # the cut: the middle of the wires' gap, or far enough forward that the cup floor in
        # front of the plug's way through it keeps min_wall in the back half
        y_plug = py + g + pd["shell"]["y1"] + c
        y_cut = min(y_pb - c, max(y_zu + gap / 2, y_plug + cfg.min_wall + 0.05))
        out.update(
            y_zu=y_zu,
            y_pb=y_pb,
            y_cut=y_cut,
            y_cell=y_cell,
            pbx=pbx,
            pbz=cs["pbz"],
            cell_x=cs["x"],
            room=cs["room"],
            pb_top=max(v["z1"] for v in boxes.values()),
            mouth=pbx + usb["x0"],
            ucy=y_pb + usb_zc,
            need=need,
        )
        return out

    # a lip at least wall + lip_r keeps a flat between its two rounds, and min_wall where they
    # meet at the cup's corners
    g = c + (cfg.lip_wall or w + cfg.lip_r)
    for _ in range(8):
        need = layers(g)["need"]
        if need <= Df + 2 * g + 1e-9:
            break
        g = (need - Df) / 2
    else:
        raise SystemExit("case: the skin's offset does not settle")

    def place(g, drop):
        """Every part for a skin grown by g, its bottom `drop` under the highest the bottom
        corner rounds allow."""
        P = layers(g)
        Ws, Rs, re, yb = W + 2 * g, R + g, er + g, -g
        y_zu, y_pb, y_cell = P["y_zu"], P["y_pb"], P["y_cell"]
        pbx, pbz, cell_x = P["pbx"], P["pbz"], P["cell_x"]
        # Zero: top toward the back, port edge down, so its microSD end at -X (local x -> X,
        # y -> Z, z -> -Y); the card sticks out past that end
        zx = -Ws / 2 + w + c - ex0
        cell_loc = frame_loc((cell_x, yb + y_cell, cell_zc), (0, 0, -1), (0, 1, 0))  # lead -X
        P.update(ucz=pbz - (usb["y0"] + usb["y1"]) / 2)
        r0 = 3.0  # Zero board corner
        cands = [
            corner_zb(zx + r0, zz + r0, r0 + c, x_flat, Rs, w),
            corner_zb(zx + L - r0, zz + r0, r0 + c, x_flat, Rs, w),
            corner_zb(cell_x - by / 2 + 1.5, cell_zc - bx / 2 + 1.5, 1.5 + c, x_flat, Rs, w),
            corner_zb(cell_x + by / 2 - 1.5, cell_zc - bx / 2 + 1.5, 1.5 + c, x_flat, Rs, w),
            # the pocket's rim on the flat of the back face, over the back bottom edge round
            z_mc - sh_ / 2 - cfg.screen_gap - (cfg.lip_r + 0.25) - re,
        ]
        for k in conn.values():  # connector lips past the port edge
            for lx in (k["x0"], k["x1"]):
                cands.append(corner_zb(zx + lx, zz + k["y0"], c, x_flat, Rs, w))
        for k, v in boxes.items():  # the power board's lowest corners
            if k == "usb_c":
                continue
            for x in (v["x0"], v["x1"]):
                cands.append(corner_zb(pbx + x, pbz - v["y1"], c, x_flat, Rs, w))
        zb = min(cands) - drop
        loc = frame_loc((pbx, yb + y_pb, pbz), (1, 0, 0), (0, 1, 0))  # y -> -Z, parts -> +Y
        cap_loc = frame_loc((cap_x0, yb + w + c + cd / 2, cap_zc), (0, 1, 0), (1, 0, 0))
        y_o = yb + sd["plate_t"] + cfg.screen_seat - sd["t"]
        scr_loc = frame_loc((x_mod, y_o, z_mc), (1, 0, 0), (0, 0, 1))
        P.update(
            zx=zx,
            zz=zz,
            cell_x=cell_x,
            cell_zc=cell_zc,
            g=g,
            Ws=Ws,
            Rs=Rs,
            re=re,
            yb=yb,
            yf=Df + g,
            zb=zb,
            scr_loc=scr_loc,
            pi_zero_w=models.place(pi_draw, frame_loc((zx, yb + y_zu, zz), (1, 0, 0), (0, -1, 0))),
            battery=models.place(bat, cell_loc),
            power_board=models.place(pb, loc),
            screen=models.place(scr, scr_loc),
            cap_plug=models.place(cap, cap_loc),
        )
        return P

    def short(P, p, zb, inset=w):
        """How far corner p is short of the clearance to the cavity (> 0: too close or out)."""
        Ws, yb, yf = P["Ws"], P["yb"], P["yf"]
        R_in, r_in = P["Rs"] - inset, P["re"] - inset
        return c - in_rounded_block(
            p, -Ws / 2 + inset, Ws / 2 - inset, yb + inset, yf - inset, zb + inset, R_in, r_in
        )

    def inside(P, name, s):
        """A part that sits in the cavity (not the screen module on the back face)."""
        if name == "screen":
            return False
        return s.bounding_box().min.Y >= P["yb"] + w - 1e-6

    def worst(P, zb):
        """The part corner nearest to leaving the cavity, of those in its back or front edge
        rounds (the rest the layers and corner_zb place)."""
        bad = (0.0, None, None)
        for name in PARTS:
            solids = P[name].solids()
            if name == "power_board":
                # its receptacle reaches into the charge port, through the wall (the children hold
                # the part's own frame; its solids, in the same order, are placed)
                kids = P[name].children
                solids = [x for k, x in zip(kids, solids, strict=True) if k.label != "power_usb_c"]
            for s in solids:
                if not inside(P, name, s):
                    continue
                for p in corners(s):
                    if P["yb"] + P["re"] <= p[1] <= P["yf"] - P["re"]:
                        continue
                    d = short(P, p, zb)
                    if d > bad[0] + 1e-6:
                        bad = (d, name, p)
        return bad

    # a corner short of room even over a bottom far below sits in a vertical edge round: grow
    # the skin; then lower the bottom until the bottom edge rounds clear every corner
    step = 0.01
    drop = 0.0
    for _ in range(1000):
        P = place(g, drop)
        d, name, p = worst(P, P["zb"] - 100)
        if d > 0:
            if p[1] < P["yb"] + P["re"]:  # a deeper skin only moves the front rounds away
                raise SystemExit(f"case: the {name} corner at {p} is in a back corner round")
            g += step
            continue
        d, name, p = worst(P, P["zb"])
        if d <= 0:
            break
        drop += step
    else:
        raise SystemExit(f"case: the {name} corner at {p} does not fit the skin's rounds")
    if cfg.lip_wall and g > c + cfg.lip_wall + 1e-9:
        raise SystemExit(
            f"case: the parts need a lip wall of {g - c:.2f}, lip_wall is {cfg.lip_wall}"
        )
    Ws, Rs, re, yb, yf, zb = P["Ws"], P["Rs"], P["re"], P["yb"], P["yf"], P["zb"]
    y_pb, y_zu, y_cell = P["y_pb"], P["y_zu"], P["y_cell"]
    plug_loc = frame_loc((px, py, z_ft), (1, 0, 0), (0, 0, 1))
    pl_plug = models.place(plug, plug_loc)

    # the module as near screen_x as its posts let it: each post, the clearance round it, clear
    # of the power board's parts that reach back to its tip
    y_post = yb + w_back - w + cfg.insert_min  # the posts' tips
    rp = r_ins + c
    bad = []
    for s_ in P["power_board"].solids():
        bb = s_.bounding_box()
        dz = max(0.0, bb.min.Z - z_mc, z_mc - bb.max.Z)
        if bb.min.Y >= y_post + c - 1e-6 or dz >= rp:
            continue
        half = math.sqrt(rp * rp - dz * dz)
        for hx, _ in sd["holes"]["points_xz"]:
            bad.append((bb.min.X - half - hx, bb.max.X + half - hx))
    cands = [cfg.screen_x] + [v for a, b_ in bad for v in (a - 0.01, b_ + 0.01)]
    ok = [x for x in cands if not any(a < x < b_ for a, b_ in bad)]
    x_mod = min(ok, key=lambda x: abs(x - cfg.screen_x))
    y_o = yb + sd["plate_t"] + cfg.screen_seat - sd["t"]
    P["scr_loc"] = frame_loc((x_mod, y_o, z_mc), (1, 0, 0), (0, 0, 1))
    P["screen"] = models.place(scr, P["scr_loc"])

    # the module's wires run straight in from the bend, short of the plug board's back face,
    # through the notch over the module (from the seam at the pocket's top edge); a plug board
    # insert whose boss would stand in their way is left out, the board keeps its other screws
    dropped = 0
    y_o = yb + sd["plate_t"] + cfg.screen_seat - sd["t"]
    y_bend = y_o + sd["plug_y"] + cb["bend_r"]
    run = py + bd["y0"] - c - y_bend
    if run < yb + w + c - y_bend:
        raise SystemExit("case: the screen wires have no room past the back wall")
    op = (x_mod - cfg.screen_notch_w / 2, x_mod + cfg.screen_notch_w / 2)
    z_n = z_mt + cfg.screen_gap + cfg.screen_notch_h
    kept = [(hx, hz) for hx, hz in holes_xz if not op[0] - r_ins < px + hx < op[1] + r_ins]
    dropped = len(holes_xz) - len(kept)
    if holes_xz and not kept:
        raise SystemExit("case: every plug board insert stands in the screen wires' way")
    holes_xz = kept
    scr = models.screen_module(db, cfg.screen, plug_size, {"w": cfg.screen_wire_w, "run": run})
    sd = scr.dims
    P["screen"] = models.place(scr, P["scr_loc"])

    # the seam: the screen pocket's top edge; the case is built as the part above it and the part
    # below it, then fused
    pocket = (z_mc - sh_ / 2 - cfg.screen_gap, z_mt + cfg.screen_gap)
    ucz, ucy, mouth = P["ucz"], P["ucy"], P["mouth"]
    zo = (ucz - o_w / 2, ucz + o_w / 2)  # the charge port's Z span
    z_tan = zb + Rs  # the bottom corner rounds' tangent: the cavity is full width above it
    z_seam = pocket[1]

    # skin, lip, cup, cavity -------------------------------------------------------------------
    if 2 * cfg.lip_r >= g - c - 0.05:
        raise SystemExit(f"case: lip_r {cfg.lip_r} leaves no flat on a {g - c:.2f} lip wall")
    skin = rounded_block(-Ws / 2, Ws / 2, yb, yf, zb, top + 1, Rs, re)
    # the back half prints back face down, and a round on the back face's edges would leave the
    # bed tangent (stair-stepped first layers, a ring of support): under its 45 degree point it
    # is a 45 degree bevel
    hb = re * (1 - 1 / math.sqrt(2))  # the round's 45 degree point over the back face

    def back_outline(t, y):
        sk = rect4(-Ws / 2 + t, Ws / 2 - t, zb + t, top + 1, (Rs - t, Rs - t, 0, 0))
        return (Plane.XZ.offset(-y) * sk).faces()[0].outer_wire()

    skin = (
        skin + Solid.make_loft([back_outline(2 * hb, yb), back_outline(hb, yb + hb)], True)
    ).clean()

    # the cup: the clean body and its panel grown by the clearance, the step between them
    # rounded twice lip_r: the lip top's round runs round that convex corner
    cup = cam.clean_lower(c, top + 1) + cam.clean_panel(c, top + 1)
    y_step = Dc + c
    px0 = pnl["u"] - pnl["w"] / 2 - c - 0.01
    px1 = pnl["u"] + pnl["w"] / 2 + c + 0.01
    pz0 = pnl["v"] - pnl["h"] / 2 - c - 0.01
    step_edges = []
    for e in cup.edges():
        bb = e.bounding_box()
        if (
            abs(bb.min.Y - y_step) < 1e-4
            and abs(bb.max.Y - y_step) < 1e-4
            and px0 < bb.min.X
            and bb.max.X < px1
            and pz0 < bb.min.Z < top
        ):
            step_edges.append(e)
    cup = fillet(step_edges, 2 * cfg.lip_r)

    base_shell = skin & box(-Ws, Ws, yb - 1, yf + 1, z_seam, top + 2)
    lip_h = None
    if wrap:
        xc = cam.f["shutter"]["u"]
        terms, xbr, ybr, deg_y = wrap_terms(cfg, cam, xc, Dc / 2, Dc, W)
        # OCC's fillets on the spline top are fragile: retry the surface, outer and inner edges
        for m_ in SURFACE_MARGINS:
            lip_top, lip_h = lip_surface(
                terms, xbr, ybr, deg_y, -Ws / 2 - m_, Ws / 2 + m_, yb - m_, yf + m_
            )
            cut_ = base_shell - solid_over(lip_top)
            shell = try_fillet(cut_, lip_edges(cut_), cfg.lip_r)
            if shell is None:
                continue
            cut_ = shell - cup
            shell = try_fillet(cut_, lip_edges(cut_, cup), cfg.lip_r)
            if shell is not None:
                break
        else:
            raise SystemExit("case: the lip's edges do not fillet")
    else:
        shell = base_shell & box(-Ws, Ws, yb - 1, yf + 1, z_seam, top)
        shell = fillet(face_at(shell, "Z", top).edges(), cfg.lip_r)
        cut_ = shell - cup
        shell = try_fillet(cut_, face_at(cut_, "Z", top).inner_wires()[0].edges(), cfg.lip_r)
        if shell is None:
            raise SystemExit("case: the lip's inner edge does not fillet")
    lower = skin & box(-Ws, Ws, yb - 1, yf + 1, zb - 1, z_seam)
    cup_section = Face(face_at(cup & box(-60, 60, -20, 40, top - 1, top), "Z", top).outer_wire())
    cavity = rounded_block(-Ws / 2 + w, Ws / 2 - w, yb + w, yf - w, zb + w, z_ceil, Rs - w, re - w)
    shell -= cavity
    lower -= cavity

    above = box(-Ws, Ws, yb - 1, yf + 1, z_seam, top + 2)
    below_seam = box(-Ws, Ws, yb - 1, yf + 1, zb - 1, z_seam)

    def cut(s):
        nonlocal shell, lower
        shell -= s
        lower -= s

    def add(s):
        """Material inside the skin, to whichever body holds it on each side of the seam."""
        nonlocal shell, lower
        s = s & skin
        if s.volume < 1e-6:
            raise SystemExit("case: material added outside the skin")
        top_part, low_part = s & above, s & below_seam
        if top_part.volume > 1e-6:
            shell += top_part
        if low_part.volume > 1e-6:
            lower += low_part

    # plug board block: hangs from the cup floor. The plug board screws into heat-set inserts in
    # a block behind it, the block over its top edge stops it going up; the block stands on the
    # seam and merges with the back wall, the stop ends at the screen wires' notch
    rr, shp = pd["rear"], pd["shell"]
    x_cut = min(blk[1], op[0])
    y_blk = yb + w - 0.01
    # the screen wires' notch: up from the seam through the back wall
    r_n = 0.5  # its top corners
    notch = rect4(op[0], op[1], z_seam - 0.01, z_n, (0, 0, r_n, r_n))
    # through the wall and the pad behind the screen pocket
    wires = along_y(notch, yb - 1, yb + w_back + 0.01)
    hz_w = [z_ft + hz for _, hz in holes_xz]
    z_low = min(hz_w) - r_ins if holes_xz else z_bt
    if z_low < z_seam - 1e-6:
        raise SystemExit("case: the plug board's inserts reach under the seam")
    z_low = z_seam  # stand on the seam
    y_front_blk = py + bd["y1"] + cfg.block_margin
    shell += box(blk[0], x_cut, y_blk, y_front_blk, z_bt, z_ceil + 0.01)  # the stop
    shell_holes = []
    if holes_xz:
        yb0 = py + bd["y0"]  # the board's back face, the block's front
        blkp = box(blk[0], x_cut, y_blk, yb0, z_low, z_bt + 0.01)
        for (hx, _), hz in zip(holes_xz, hz_w, strict=True):
            hxw = px + hx
            blkp += box(hxw - r_ins, hxw + r_ins, y_blk, yb0, z_low, hz)
            blkp += cyl(r_ins, (hxw, y_blk, hz), (hxw, yb0, hz))
        blkp &= box(blk[0], blk[1], y_blk - 1, yb0, z_low, z_ceil + 1)
        shell += blkp
        # inserts from the block's front, the screws through the board from the front
        board_t = bd["y1"] - bd["y0"]
        back = yb + w  # the back wall's inner face
        plug_screw = kit_screw(cfg, board_t + cfg.insert_len, board_t + (yb0 - back) - 0.5)
        tip = py + bd["y1"] - plug_screw
        for (hx, _), hz in zip(holes_xz, hz_w, strict=True):
            hxw = px + hx
            limit = yb  # behind the block: the back wall
            ins_end = yb0 - cfg.insert_min
            ins_open = ins_end - limit < cfg.min_wall  # no floor fits: through
            # on past the insert by insert_relief, to min_wall over the back face: room for the
            # plastic the insert pushes ahead, so the screw's tip does not push the insert out
            relief = max(yb0 - cfg.insert_len - cfg.insert_relief, limit + cfg.min_wall)
            ins_end = min(ins_end, relief)
            end = min(ins_end, tip - 0.5)
            if ins_open:  # through to the back face
                ins_end = end = limit
            open_back = not ins_open and end - limit < cfg.min_wall
            if open_back:
                end = limit
            shell -= cyl(cfg.insert_d / 2, (hxw, yb0 + 0.01, hz), (hxw, ins_end, hz))
            if end < ins_end:
                shell -= cyl(cfg.screw_d / 2, (hxw, ins_end + 0.01, hz), (hxw, end, hz))
            shell_holes.append(
                Hole(
                    (hxw, yb0, hz),
                    (hxw, limit + 0.02 if ins_open else ins_end, hz),
                    cfg.insert_d,
                    "plug board insert",
                    floor=0.0 if ins_open else cfg.min_wall,
                    floor_d=cfg.screw_d if end < ins_end else 0.0,
                    skip=0.1,  # the block's front set back for the plug's rear
                )
            )
            if end < ins_end:
                fl = 0.0 if open_back else cfg.min_wall
                p1 = (hxw, limit + 0.02 if open_back else end, hz)
                shell_holes.append(
                    Hole((hxw, ins_end, hz), p1, cfg.screw_d, "plug board screw", fl)
                )
    # the plug's rear stands 0.03 behind the board: the block's front set back 0.05 (the plug
    # touches the case on purpose), the way through the stop block over it
    y_rear = min(py + rr["y0"], py + bd["y0"]) - 0.05 if holes_xz else py + rr["y0"] - c
    if holes_xz:
        shell -= box(blk[0] - 0.01, blk[1] + 0.01, y_rear, py + bd["y0"] + 0.01, z_low - 0.01, z_bt)
    shell -= box(
        px + rr["x0"] - c,
        px + rr["x1"] + c,
        y_rear,
        py + rr["y1"] + c,
        z_ft + rr["z0"] - 0.01,
        z_ft + 0.5,
    )
    pw, pt = shp["x1"] - shp["x0"] + 2 * c, shp["y1"] - shp["y0"] + 2 * c
    shell -= along_z(rrect(pw, pt, pt / 2 - 0.01, px, py), z_ceil - 1, z_ft + 1)

    extra = []
    thin_ok = []  # (half, zone, why); the half is picked by Y at the end
    # charge port: a plug-sized opening through the -X wall round the receptacle's front, on
    # in past the inner face where the corner and bottom rounds bulge; the cable's overmold
    # stays outside against the side
    k = along_x(rrect(o_h, o_w, o_h / 2 - 0.01, yb + ucy, ucz), -Ws / 2 - 1, -Ws / 2 + re)
    cut(k)
    if ucy - o_h / 2 < re or zo[0] < z_tan:
        # its rim meets the back corner and bottom corner rounds at about 80 degrees, not
        # square: the rays read thin only right at the edge. The rim, not the opening's
        # sides further in, is left out of the wall check
        zone = (
            -Ws / 2 - 1,
            -Ws / 2 + w - 0.2,
            yb + ucy - o_h / 2 - 0.5,
            yb + ucy + o_h / 2 + 0.5,
            zo[0] - 0.5,
            zo[1] + 0.5,
        )
        thin_ok.append((None, zone, "charge port's rim on the corner rounds, not square"))
    cable = rrect(cfg.plug_body_h, cfg.plug_body_w, 1.0, yb + ucy, ucz)
    ko["charge_cable"] = along_x(cable, -Ws / 2 - cfg.plug_body_len, -Ws / 2)
    extra.append(
        (
            "cable",
            checks.parts_clear(
                "cable",
                "charge cable overmold outside the port",
                ko["charge_cable"],
                ("camera",),
            ),
        )
    )

    lower_holes = []
    # the screen module: a pocket for its back plate with an even gap round it, open at its
    # top edge (the seam), a pad behind it, a boss with an insert at each screw; over its top
    # edge a notch in the back wall for the wires
    pt_, rs = sd["plate_t"], sd["r"]
    depth = pt_ + cfg.screen_seat
    gs = cfg.screen_gap
    po = rrect(sw_ + 2 * gs, sh_ + 2 * gs, rs + gs, x_mod, z_mc)
    hw_o, r_o = sw_ / 2 + gs + w, rs + gs + w
    pad_o = rect4(
        x_mod - hw_o,
        x_mod + hw_o,
        z_mc - sh_ / 2 - gs - w,
        z_seam + cfg.lap_h,
        (r_o, r_o, 0, 0),
    )
    # one pad on past the pocket's top edge, none over the wires' notch
    lower += (along_y(pad_o, yb + 0.01, yb + depth + w) & skin) - box(
        op[0], op[1], yb - 1, yb + depth + w + 1, z_seam, z_seam + cfg.lap_h + 1
    )
    y_tip = yb + depth + cfg.insert_min  # each boss as deep as its insert needs, open
    for hxz in sd["holes"]["points_xz"]:
        bx_, bz_ = x_mod + hxz[0], z_mc + hxz[1]
        lower += cyl(r_ins, (bx_, yb + w, bz_), (bx_, y_tip, bz_)) & skin
    hw_p = sw_ / 2 + gs
    # the pocket, its rim's round and the wires' notch are cut once the parts above and below
    # the seam are fused (the pocket's top edge is on the seam)
    pocket_cut = along_y(po, yb - 1, yb + depth) + box(
        x_mod - hw_p, x_mod + hw_p, yb - 1, yb + depth, z_mc, z_seam
    )
    screen_screw = kit_screw(cfg, pt_ + cfg.insert_len, pt_ + cfg.insert_min)
    for hxz in sd["holes"]["points_xz"]:
        bx_, bz_ = x_mod + hxz[0], z_mc + hxz[1]
        lower -= cyl(cfg.insert_d / 2, (bx_, yb + depth - 0.01, bz_), (bx_, y_tip + 0.01, bz_))
        lower_holes.append(
            Hole((bx_, yb + depth, bz_), (bx_, y_tip, bz_), cfg.insert_d, "screen insert")
        )
    extra.append(("screen", lambda d, cfg_: screen_free(d, cfg_)))

    # holders, each the clearance from its part, each a prism along Y off its half's outer
    # wall (each half prints lying on that face). Back: the Zero lies top side down on a column
    # at each mounting hole (the lower two carry the closing screws), on a post under its port
    # edge in the free stretch nearest each end, a fin past its +X end. Front: the cell in a rib
    # at each end over a foot under it; the power board's receptacle in the charge port with a
    # block in front of it, and below the cell two ledges in front of its bottom edge with a lip
    # under it, the +X one on past its end.
    hw_, ft_ = cfg.holder_w, cfg.holder_foot
    zx, zz, cell_x, cell_zc = P["zx"], P["zz"], P["cell_x"], P["cell_zc"]
    y_cut = yb + P["y_cut"]
    yzu = yb + y_zu  # the Zero's underside
    t_pi = cfg.zero_pcb or pi.psize[2]
    yzt = yzu - t_pi  # its top side
    yw0 = yb + w - 0.01  # the back wall's inner face
    yfw = yf - w + 0.01  # the front wall's inner face
    holders = []
    press = []  # (body, part, zone, why): where a holder presses on its part
    free_ = [(pi.corner_r, L - pi.corner_r)]
    for k in conn.values():
        lo_, hi_ = k["x0"] - c, k["x1"] + c
        free_ = [(a, b) for a0, b0 in free_ for a, b in ((a0, min(b0, lo_)), (max(a0, hi_), b0))]
        free_ = [(a, b) for a, b in free_ if b - a >= hw_]
    if not free_:
        raise SystemExit("case: no stretch of the Zero's port edge free for a post")
    zposts = [zx + (a + b) / 2 for a, b in {min(free_), max(free_)}]  # board x runs toward +X
    for xp in zposts:
        holders.append(("Zero post", box(xp - hw_ / 2, xp + hw_ / 2, yw0, yzu, zb - 1, zz - c)))
    x_end = zx + L + c + cfg.zero_end_gap  # the end fin's face, past the Zero's +X end
    holders.append(("Zero end fin", box(x_end, x_end + hw_, yw0, yzu, zb - 1, zz + Wd / 2)))
    # a column on each mounting hole's copper ring, wider behind the Zero's keep-out for the
    # closing screws' heads
    r_ring = pi.pad_d / 2 - c
    r_col = cfg.head_d / 2 + cfg.min_wall
    y_wide = yzu - max(k["z1"] for k in conn.values()) - c  # behind the Zero's ports
    holes_pi = [(zx + hx, zz + hy) for hx, hy in pi.holes]
    for x_, z_ in holes_pi:
        col = cyl(r_ring, (x_, yw0, z_), (x_, yzt - cfg.close_play, z_))
        col += cyl(r_col, (x_, yw0, z_), (x_, y_wide, z_))
        holders.append(("Zero column", col))
        zone = (x_ - r_ring - c, x_ + r_ring + c, y_wide - c, yzt + c, z_ - r_ring - c)
        zone += (z_ + r_ring + c,)
        press.append(("back", "pi_zero_w", zone, "column on its mounting hole's copper ring"))

    cx0, cx1 = cell_x - by / 2, cell_x + by / 2
    cz0 = cell_zc - bx / 2

    def y_front(x0, x1, z1):
        """Where a holder from the floor up to z1 over x0..x1 starts: in front of the cell's back
        face, or of a power board part beside it, by the clearance."""
        y_ = yc0
        for v in tall.values():
            vx0, vx1 = P["pbx"] + v["x0"], P["pbx"] + v["x1"]
            if vx0 < x1 + c and vx1 > x0 - c and P["pbz"] - v["y1"] < z1 + c:
                y_ = max(y_, yb + y_pb + v["z1"] + c)
        return y_

    yc0 = yb + y_cell + c
    lead_z0 = cell_zc - bat.dims["lead_w"] / 2
    for x_stop, x_foot, z_top in (
        (cx1 + c, cx1 - ft_, cell_zc),
        (cx0 - c, cx0 + ft_, lead_z0 - c),
    ):
        sgn = 1 if x_stop > cell_x else -1
        x_out = x_stop + sgn * hw_
        rx0, rx1 = min(x_stop, x_out), max(x_stop, x_out)
        fx0, fx1 = min(x_foot, x_out), max(x_foot, x_out)
        rib = box(rx0, rx1, y_front(rx0, rx1, z_top), yfw, zb - 1, z_top)
        rib += box(fx0, fx1, y_front(fx0, fx1, cz0 - c), yfw, zb - 1, cz0 - c)
        holders.append(("cell rib", rib))

    pcb = pf["pcb"]
    pbx_, pbz_ = P["pbx"], P["pbz"]
    yp1 = yb + y_pb  # its flat side, toward the Zero
    yp0 = yp1 + t_pb  # its parts side
    x_p1 = pbx_ + pcb["x1"]
    z_pt, z_pb = pbz_ - pcb["y0"], pbz_ - pcb["y1"]  # its top (header) and bottom edges
    x_u1 = pbx_ + usb["x1"]  # the receptacle's +X end
    z_u0, z_u1 = pbz_ - usb["y1"], pbz_ - usb["y0"]
    y_uf = yp1 + usb["z1"]  # the receptacle's front
    x_bl = min(x_u1, cx0 - bat.dims["lead_len"] - c)  # clear of the cell's lead
    holders.append(("power board block -X", box(-Ws / 2, x_bl, y_uf + c, yfw, z_u0, z_u1)))
    z_l1 = cz0 - c  # the ledges' top, under the cell
    if z_l1 - z_pb < cfg.min_wall:
        raise SystemExit("case: the power board's bottom edge is not below the cell")
    strip = [(pcb["x0"], pcb["x1"])]
    for v in tall.values():
        if pbz_ - v["y0"] > z_pb - c and pbz_ - v["y1"] < z_l1 + c:  # a part in the strip
            lo_, hi_ = v["x0"] - c, v["x1"] + c
            strip = [
                (a, b) for a0, b0 in strip for a, b in ((a0, min(b0, lo_)), (max(a0, hi_), b0))
            ]
            strip = [(a, b) for a, b in strip if b - a >= hw_]
    if not strip:
        raise SystemExit("case: no stretch of the power board's bottom edge free for a ledge")
    z_lip = z_pb - c - cfg.min_wall
    for a, b_ in sorted({min(strip), max(strip)}):
        x0_, x1_ = pbx_ + a, pbx_ + b_
        ledge = box(x0_, x1_, yp0 + c, yfw, z_lip, z_l1)
        ledge += box(x0_, x1_, yp1, yp0 + c + 0.01, z_lip, z_pb - c)  # the lip under the edge
        if b_ >= pcb["x1"] - 1e-6:  # on past the +X end
            ledge += box(x_p1 + c, x_p1 + c + hw_, yp1, yfw, z_lip, z_l1)
        holders.append(("power board ledge", ledge))
    for _, h_ in holders:
        add(h_)

    # closing screws: from the back face through a column and the Zero's lower mounting holes
    # into a heat-set insert at the back of a boss on the front half that reaches back to the
    # Zero's underside; heads counterbored in the back face
    z_low_h = min(z for _, z in holes_pi)
    close = [(x_, z_) for x_, z_ in holes_pi if abs(z_ - z_low_h) < 1e-6]
    y_head = yb + cfg.head_h  # the counterbore's floor
    y_ins0 = yzu + cfg.close_play  # the boss's back face, on the Zero's underside
    y_ins1 = y_ins0 + cfg.insert_len
    close_screw = kit_screw(cfg, y_ins1 - 0.5 - y_head, yfw - cfg.min_wall - y_head)
    y_tip = y_head + close_screw
    if y_tip < y_ins1 - 1.5:
        raise SystemExit(f"case: an M2.5 x {close_screw:g} from the back reaches too little insert")

    def halves(d, cfg_):
        """Each part lies in one half: the cut runs through the wires' gap only."""
        out = []
        for name, half in (
            ("pi_zero_w", "back"),
            ("usb_c_plug", "back"),
            ("screen", "back"),
            ("power_board", "front"),
            ("battery", "front"),
        ):
            bb_ = d.part(name).shape.bounding_box()
            ok = (
                (bb_.max.Y <= y_cut - c + 1e-6)
                if half == "back"
                else (bb_.min.Y >= y_cut + c - 1e-6)
            )
            out.append(
                checks.line(
                    "assembly",
                    f"{name} in the {half} half",
                    ok,
                    f"Y {bb_.min.Y:.2f} to {bb_.max.Y:.2f}, cut at Y {y_cut:.2f}",
                )
            )
        return out

    extra.append(("assembly", halves))
    extra.append(
        (
            "solid",
            lambda d, cfg_: [
                checks.line(
                    "solid",
                    f"{b.name} is one valid solid",
                    b.solid.is_valid and len(b.solid.solids()) == 1,
                    f"valid {b.solid.is_valid}, {len(b.solid.solids())} solid(s)",
                )
                for b in d.bodies
            ],
        )
    )
    dip = (yl - cfg.lanyard_w / 2 - cfg.lanyard_ramp, yl + cfg.lanyard_w / 2 + cfg.lanyard_ramp)
    dip = dip if wrap and cfg.lanyard else None
    extra.append(("cup", lambda d, cfg_: cup_lines(cam, cup_section, lip_h, top, dip, cfg_)))
    extra.append(("fine", lambda d, cfg_: checks.fine_features(d, d.bodies[0].name, 0.0)))
    if wrap:
        extra.append(("wrap", lambda d, cfg_: wrap_lines(d, cfg_, ko, Ws, yb, yf)))

    # one body, then two halves at the cut --------------------------------------------------
    whole = shell.fuse(lower).clean()
    if len(whole.solids()) != 1:
        raise SystemExit("case: the parts above and below the seam do not join into one body")
    whole = whole - pocket_cut - wires
    # the pocket's rim and the notch's edges in the back face rounded like the lip
    rim = [
        e
        for e in whole.edges()
        if abs(e.bounding_box().min.Y - yb) < 1e-4
        and abs(e.bounding_box().max.Y - yb) < 1e-4
        and e.bounding_box().min.Z > pocket[0] - 0.01
        and e.bounding_box().max.Z < z_n + 0.01
        and abs((e.bounding_box().min.X + e.bounding_box().max.X) / 2 - x_mod) < hw_p + 0.01
    ]
    rounded = try_fillet(whole, rim, cfg.lip_r)
    if rounded is None:
        raise SystemExit("case: the screen pocket's rim does not round")
    whole = rounded
    lt_v = cfg.lap_tongue or cfg.min_wall
    t_out = w + cfg.lap_gap  # the tongue's outer face, lap_gap inside the front half's walls
    t_in_v = t_out + lt_v

    def zt(t):  # a section t in from the skin: the cavity's ceiling t - w lower
        return z_ceil - (t - w)

    def sec(t, y0, y1):
        sk = rect4(-Ws / 2 + t, Ws / 2 - t, zb + t, zt(t), (Rs - t, Rs - t, 0, 0))
        return along_y(sk, y0, y1)

    def sec_wire(t, y):
        sk = rect4(-Ws / 2 + t, Ws / 2 - t, zb + t, zt(t), (Rs - t, Rs - t, 0, 0))
        return (Plane.XZ.offset(-y) * sk).faces()[0].outer_wire()

    lap_ = None
    band_v = dt = 0.0
    if cfg.lap:
        y1_ = y_cut + cfg.lap_h
        tongue = sec(t_out, y_cut - 0.01, y1_) - sec(t_in_v, y_cut - 1, y1_ + 1)
        # the band under it: the back half's wall thickened to the tongue's inner face, min_wall
        # straight under the tongue, then out to the wall at 45 degrees (it prints without
        # support)
        dt = t_in_v - w
        band_v = dt + cfg.min_wall
        y_sup = y_cut - band_v + dt  # (y_sup - dt is where the band starts)
        y_b0 = y_sup - dt - 0.01  # the slope from the wall's inner face, 0.01 into it
        frustum = Solid.make_loft([sec_wire(w - 0.01, y_b0), sec_wire(t_in_v, y_sup)], True)
        support = sec(w - 0.01, y_b0, y_cut) - frustum
        support -= sec(t_in_v, y_sup - 0.01, y_cut + 1)
        lap_ = (tongue + support) & skin
        # cut back where a part, or a holder in the front half, stands in its way
        for name in PARTS:
            for s_ in P[name].solids():
                b_ = s_.bounding_box()
                if b_.max.Y < y_sup - dt - c or b_.min.Y > y1_ + c:
                    continue
                lap_ -= box(
                    b_.min.X - c,
                    b_.max.X + c,
                    b_.min.Y - c,
                    b_.max.Y + c,
                    b_.min.Z - c,
                    b_.max.Z + c,
                )
        for s_ in pl_plug.solids():
            b_ = s_.bounding_box()
            lap_ -= box(
                b_.min.X - c, b_.max.X + c, b_.min.Y - c, b_.max.Y + c, b_.min.Z - c, b_.max.Z + c
            )
        lap_ -= box(blk[0] - 1, blk[1] + 1, y_sup - dt - 1, y1_ + 1, z_ceil - lt_v - 3, z_ceil + 1)
        lg = cfg.lap_gap
        for x_, z_ in close:  # round each boss out to the floor and the nearer side: no thin
            # piece of tongue left on the bottom corner round
            r_ = r_boss + lg + lt_v + 1.0
            x_fl = Ws / 2 - Rs  # where the bottom corner round meets the flat: a square end
            x0_, x1_ = (
                (min(x_ - r_, x_fl), Ws / 2 + 1) if x_ > 0 else (-Ws / 2 - 1, max(x_ + r_, -x_fl))
            )
            lap_ -= box(x0_, x1_, y_ins0 - 1, y1_ + 1, zb - 1, z_ + r_)
        for _, h_ in holders:
            b_ = h_.bounding_box()
            if b_.min.Y > y_cut - 1e-6:
                lap_ -= box(
                    b_.min.X - lg,
                    b_.max.X + lg,
                    b_.min.Y - lg,
                    b_.max.Y + 1,
                    b_.min.Z - lg,
                    b_.max.Z + lg,
                )
    # closing screws at the top corners, from the back face like the lower pair: a column on the
    # back half from the back wall to the cut, a boss on the front half from the cut with its
    # insert, hanging from the ceiling and joined to the side wall. The cell fills the front half
    # inboard of its ends, so each boss's front ends on a 45 degree face from the cell's end (it
    # prints without support, the front half front face down); the insert's ring has to end
    # before it, which sets how far out the screws sit (the same on both sides, the -X side
    # needing less). Under the cup floor by min_wall; the heads sink below the back face's
    # corner round there.
    r_hd = cfg.head_d / 2
    z_t = z_ft - cfg.min_wall - r_hd
    y_bat = yb + y_cell - c  # the cell's back face, less the clearance
    y_ti1 = y_cut + cfg.insert_len
    u_end = {1: cx1 + c, -1: -(cx0 - c)}  # each side's cell end, outward
    u_t = max(u + r_boss - (y_bat - y_ti1) for u in u_end.values())
    if u_t + r_hd > Ws / 2 - cfg.min_wall:
        raise SystemExit("case: no room for the top closing screws beside the cell")
    tops = [(s * u_t, z_t) for s in (-1, 1)]

    def back_face(u):
        """The back face's Y at X = u by a vertical edge: the flat, the bevel, the round."""
        d = Ws / 2 - u  # in from the side
        v = re - math.sqrt(re * re - (re - d) ** 2) if d < re else 0.0
        if d >= hb:
            v = min(v, max(0.0, 2 * hb - d))
        return yb + v

    y_head_t = back_face(u_t + r_hd) + cfg.head_h
    top_screw = kit_screw(cfg, y_ti1 - 0.5 - y_head_t, y_ti1 + cfg.insert_relief - 0.5 - y_head_t)
    if y_head_t + top_screw < y_ti1 - 1.5:
        raise SystemExit(f"case: an M2.5 x {top_screw:g} at the top reaches too little insert")
    r_cut = r_boss + cfg.lap_gap + lt_v + 1.0
    if lap_ is not None:  # the tongue cut back round each boss to the ceiling and the side
        for s in (-1, 1):
            x0_, x1_ = sorted((s * (u_t - r_cut), s * (Ws / 2 + 1)))
            lap_ -= box(x0_, x1_, y_cut, y_cut + cfg.lap_h + 1, z_t - r_cut, z_ceil + 1)

    def prism_z(pts, z0, z1):
        """Points in XY, either way round (counterclockwise faces +Z, the way it extrudes)."""
        area = sum(
            x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1], strict=True)
        )
        pts = pts if area > 0 else pts[::-1]
        return along_z(poly_rounded(pts, [0.0] * len(pts)), z0, z1)

    top_back, top_front = [], []
    under_ceiling = box(-Ws, Ws, yb - 1, yf + 1, zb, z_ceil + 0.01)
    for s in (-1, 1):
        x_ = s * u_t
        col = cyl(r_hd + cfg.min_wall, (x_, yw0, z_t), (x_, y_cut, z_t))
        top_back.append(col & under_ceiling & skin)
        boss = cyl(r_boss, (x_, y_cut, z_t), (x_, yf, z_t))
        boss += box(*sorted((x_, s * Ws)), y_cut, yf, z_t - r_boss, z_ceil + 0.01)
        # in front of the cell's back face only outward of its end, at 45 degrees
        u0, u1, ue = u_t - r_boss - 1, Ws / 2 + 1, u_end[s]
        wedge = prism_z(
            [
                (s * u0, y_cut - 1),
                (s * u1, y_cut - 1),
                (s * u1, y_bat + u1 - ue),
                (s * u0, y_bat + u0 - ue),
            ],
            zb,
            z_ceil + 1,
        )
        top_front.append(boss & wedge & under_ceiling & skin)

    back = whole & box(-Ws, Ws, yb - 1, y_cut, zb - 1, top + 2)

    front = whole & box(-Ws, Ws, y_cut, yf + 1, zb - 1, top + 2)
    if lap_ is not None:
        back += lap_
    for s_ in top_back:
        back += s_
    for s_ in top_front:
        front += s_

    # the power board's +X top corner has nothing behind it (its joints face the Zero's across
    # the wires' gap): a post from the screen pad, the clearance short of its flat side, under
    # the screen wires and over the Zero's top edge, clear of the plug board at -X
    if cfg.pb_stop:
        kids = zip(P["screen"].children, P["screen"].solids(), strict=True)
        cable = next(s_ for k_, s_ in kids if k_.label == "screen_cable").bounding_box()
        xs1 = x_p1 - c
        xs0 = xs1 - hw_
        zs0 = P["pi_zero_w"].bounding_box().max.Z + c
        zs1 = min(cable.min.Z - c, z_pt)
        if xs0 < pl_plug.bounding_box().max.X + c or zs1 - zs0 < cfg.min_wall:
            raise SystemExit("case: no room for the power board's stop behind its +X top corner")
        stop = box(xs0, xs1, yb + w_back - 0.01, yp1 - c, zs0, zs1)
        back += stop & skin
        stop_note = (
            f"power board stop: post X {xs0:.2f} to {xs1:.2f}, Z {zs0:.2f} to {zs1:.2f}, from the "
            f"screen pad to Y {yp1 - c:.2f}, {c:g} behind the board's flat side"
        )

    # the closing screws: counterbore and way through the back half; the boss and its insert's
    # hole in the front half, on past the insert by insert_relief (and past the screw's tip)
    back_holes, front_holes = [], []
    for h_ in shell_holes + lower_holes:
        (back_holes if h_.p0[1] < y_cut else front_holes).append(h_)
    y_rel = max(y_ins1 + cfg.insert_relief, y_tip + 0.5)
    for x_, z_ in close:
        r_ = cfg.head_d / 2 + 0.6
        zone = (x_ - r_, x_ + r_, yb - 1, yb + hb + 0.1, z_ - r_, z_ + r_)
        thin_ok.append(("back", zone, "a closing counterbore's rim through the back bevel"))
        back -= cyl(cfg.head_d / 2, (x_, yb - 1, z_), (x_, y_head, z_))
        back -= cyl(cfg.screw_d / 2, (x_, y_head - 0.01, z_), (x_, y_cut + 1, z_))
        front += cyl(r_boss, (x_, y_ins0, z_), (x_, yfw, z_))
        front -= cyl(cfg.insert_d / 2, (x_, y_ins0 - 0.01, z_), (x_, y_rel, z_))
        back_holes += [
            Hole(
                (x_, yb, z_),
                (x_, y_head, z_),
                cfg.head_d,
                "closing screw head",
                floor=cfg.min_wall,
                floor_d=cfg.screw_d,
                skip=cfg.head_h - 0.01,  # its rim opens onto the back face's corner round
            ),
            Hole((x_, y_head, z_), (x_, yzt - cfg.close_play, z_), cfg.screw_d, "closing screw"),
        ]
        front_holes.append(
            Hole((x_, y_ins0, z_), (x_, y_rel, z_), cfg.insert_d, "closing insert", cfg.min_wall)
        )
        zone = (x_ - r_boss - c, x_ + r_boss + c, yzu - c, y_ins0 + c, z_ - r_boss - c)
        zone += (z_ + r_boss + c,)
        press.append(("front", "pi_zero_w", zone, "boss on its mounting hole, under the Zero"))
    # the top pair. The insert's hole goes on insert_relief past it; where that leaves less than
    # min_wall before the boss's 45 degree face, it goes on through it. The counterbores' outer
    # rims sit on the back face's corner round and the face meets them at a slant, as does the
    # 45 degree face where a hole comes through it: both are left out of the wall check.
    u_rim = u_t + r_hd + cfg.min_wall  # the column's ring is whole from where the round passes
    ri = cfg.insert_d / 2
    for s, (x_, z_) in zip((-1, 1), tops, strict=True):
        back -= cyl(r_hd, (x_, yb - 1, z_), (x_, y_head_t, z_))
        back -= cyl(cfg.screw_d / 2, (x_, y_head_t - 0.01, z_), (x_, y_cut + 1, z_))
        y_r = y_ti1 + cfg.insert_relief
        face_in = y_bat + (u_t - ri) - u_end[s]  # the 45 degree face over the hole's inner edge
        if face_in - y_r < cfg.min_wall:
            y_r = y_bat + (u_t + ri) - u_end[s] + 0.5
            x0_, x1_ = sorted((s * (u_t - r_boss), s * (u_t + r_boss)))
            zone = (x0_, x1_, face_in - cfg.min_wall, y_r, z_ - r_boss, z_ + r_boss)
            thin_ok.append(("front", zone, "a top insert's relief through the 45 degree face"))
        front -= cyl(ri, (x_, y_cut - 0.01, z_), (x_, y_r, z_))
        x0_, x1_ = sorted((s * (u_t + r_hd - cfg.min_wall), s * (u_t + r_hd + 0.5)))
        zone = (x0_, x1_, yb - 1, y_head_t, z_ - r_hd - 0.5, z_ + r_hd + 0.5)
        thin_ok.append(("back", zone, "a top counterbore's rim on the back corner round"))
        back_holes += [
            Hole(
                (x_, yb, z_),
                (x_, y_head_t, z_),
                cfg.head_d,
                "top screw head",
                floor=cfg.min_wall,
                floor_d=cfg.screw_d,
                skip=back_face(u_rim) - yb,  # its rim sits on the back face's corner round
            ),
            Hole((x_, y_head_t, z_), (x_, y_cut, z_), cfg.screw_d, "top screw"),
        ]
        front_holes.append(Hole((x_, y_cut, z_), (x_, y_ti1, z_), cfg.insert_d, "top insert"))
    back, front = back.clean(), front.clean()

    e = cfg.explode
    bodies = [
        Body(
            "back",
            back,
            BACK_RGB,
            (90.0, 0.0, 0.0),
            (0, -20 * e, 0),
            "printed back face down",
            back_holes,
        ),
        Body(
            "front",
            front,
            FRONT_RGB,
            (-90.0, 0.0, 0.0),
            (0, 20 * e, 0),
            "printed front face down",
            front_holes,
        ),
    ]
    parts = [
        Placed("camera", cam.model(), (0, 0, 60 * e), "camera"),
        Placed("usb_c_plug", pl_plug, (0, -20 * e, 0)),
        Placed("power_board", P["power_board"], (0, 20 * e, 0)),
        Placed("screen", P["screen"], (0, -40 * e, 0)),
        Placed("pi_zero_w", P["pi_zero_w"], (0, -10 * e, 0)),
        Placed("battery", P["battery"], (0, 35 * e, 0)),
    ]
    hardware = []

    def hw(item, qty, use):
        for h_ in hardware:
            if h_["item"] == item:
                h_["qty"] += qty
                h_["use"] += f"; {use}"
                return
        hardware.append({"item": item, "qty": qty, "use": use})

    kit = "M2.5 x {:g} socket head screw"
    if holes_xz:
        hw(kit.format(plug_screw), len(holes_xz), "plug board to the back half, from its front")
        hw(f"M2.5 heat-set insert, 3.4 long, {cfg.insert_pn}", len(holes_xz), "plug board block")
    hw(
        kit.format(close_screw),
        len(close),
        "the halves, from the back face through the Zero's lower mounting holes",
    )
    hw(f"M2.5 heat-set insert, 3.4 long, {cfg.insert_pn}", len(close), "the front half's bosses")
    hw(kit.format(top_screw), len(tops), "the halves at the top corners, from the back face")
    hw(f"M2.5 heat-set insert, 3.4 long, {cfg.insert_pn}", len(tops), "the front half's top bosses")
    nh = len(sd["holes"]["points_xz"])
    hw(kit.format(screen_screw), nh, "screen module to the back half")
    hw(f"M2.5 heat-set insert, 3.4 long, {cfg.insert_pn}", nh, "screen bosses")
    D = yf - yb
    notes = [
        f"case {Ws:.2f} x {D:.2f} x {top - zb:.1f} mm, {z_ft - zb:.1f} under the camera: the "
        f"camera's lower body ({W:.1f} x {Df:.1f}, corners r {R:g}, edges r "
        f"{er:g}) grown {g:.3f} all round",
    ]
    if wrap:
        lan = (
            f", lanyard dip {cfg.lanyard_h:g} ({cfg.lanyard_w:g} wide at Y {yl:.2f}, "
            f"{cfg.lanyard_ramp:g} rises)"
            if cfg.lanyard
            else ""
        )
        notes.append(
            f"wrap lip: sides {cfg.wrap_side:g}, front {cfg.wrap_front:g}, back dip "
            f"{cfg.wrap_back:g} ({cfg.wrap_dip:g} wide, {cfg.wrap_ramp:g} rises){lan}, lip wall "
            f"{g - c:.3f}, top edges r {cfg.lip_r:g}"
        )
    else:
        notes.append(f"cup {top:g} deep all round, lip wall {g - c:.3f}, lip edges r {cfg.lip_r:g}")
    notes.append(f"cup: the clean body and panel grown {c:g}")
    notes.append(
        f"cut at Y {y_cut:.2f} ({P['y_cut']:.2f} from the back face), in the wires' gap ({gap:g}) "
        f"between the Zero's underside ({y_zu:.2f}) and the power board's flat side ({y_pb:.2f})"
        + (
            f"; lap: a {lt_v:g} tongue from the back half {cfg.lap_h:g} into the front half, "
            f"{cfg.lap_gap:g} inside its walls, on a band {band_v:.2f} deep that thickens them, "
            f"its back {dt:.2f} sloped at 45 degrees"
            if cfg.lap
            else "; no lap, the faces meet flush"
        )
    )
    notes.append(
        f"layers from the back face: screen pad to {w_back:.2f}, Zero {y_zu - t_pi:.2f} to "
        f"{y_zu:.2f} (top toward the back), power board {y_pb:.2f} to {y_pb + t_pb:.2f} (parts "
        f"toward the front, {P['room']:.2f} under the cell), cell {y_cell:.2f} to "
        f"{y_cell + bz:.2f}, front wall {D - w:.2f} to {D:.2f}"
    )
    notes.append(
        f"cell at X {cell_x:.2f}, its lead at -X; power board's header edge at Z {z_pt:.2f}, its "
        f"bottom edge at Z {z_pb:.2f}, {z_l1 - z_pb:.2f} under the cell for its ledges"
    )
    notes.append(
        f"charge port in -X at Y {yb + ucy:.1f} Z {ucz:.1f}, {o_w:.2f} x {o_h:.2f}, the "
        f"receptacle's face {mouth + Ws / 2:.2f} in from the side; 5 V switch inside, set before "
        "closing; the cell's lead to the power board's header (pin 3 BAT, pin 2 GND)"
    )
    notes.append(
        "closing screws at "
        + ", ".join(f"X {x:.2f} Z {z:.2f}" for x, z in close)
        + f": M2.5 x {close_screw:g} from the back face, heads in {cfg.head_d:g} "
        f"counterbores {cfg.head_h:g} deep, inserts Y {y_ins0:.2f} to {y_ins1:.2f}, tips at "
        f"Y {y_tip:.2f}, holes to Y {y_rel:.2f}"
    )
    notes.append(
        f"top closing screws at X +-{u_t:.2f} Z {z_t:.2f}: M2.5 x {top_screw:g} from the back "
        f"face, counterbores to Y {y_head_t:.2f} (the corner round), inserts Y {y_cut:.2f} to "
        f"{y_ti1:.2f}, tips at Y {y_head_t + top_screw:.2f}; the bosses' front faces at 45 "
        f"degrees from the cell's ends (X {cx0 - c:.2f}, {cx1 + c:.2f}) at Y {y_bat:.2f}"
    )
    if cfg.pb_stop:
        notes.append(stop_note)
    notes.append(
        f"screen module on the back face: centre X {x_mod:.2f} Z {z_mc:.2f} (top edge "
        f"{-z_mt:.2f} under the camera), pocket {depth:.2f} deep, gap {cfg.screen_gap:g} round "
        f"it; wires' notch over it X {op[0]:.2f} to {op[1]:.2f}"
        + (f"; {dropped} plug board insert left out of the wires' way" if dropped else "")
    )
    notes.append(
        "holders: "
        + "; ".join(
            f"{n} X {lo[0]:.1f} to {hi[0]:.1f}, Y {lo[1]:.1f} to {hi[1]:.1f}"
            for n, (lo, hi) in ((n, bbox(h_ & skin)) for n, h_ in holders)
        )
    )
    notes.append(f"plug {cfg.plug}; power {cfg.power}")
    # close-ups: the lip, the cut from the side and with the halves apart, the closing screws'
    # heads, the screen
    zl = 5.0
    apart = Location((0, 25.0, 0))
    xm = sum(x for x, _ in close) / len(close)
    closeups = [
        Closeup("lip-front", (Ws / 2 - 10, yf - 4, zl), (0.9, 1.0, 0.75), 50.0, "lip, front +X"),
        Closeup("lip-back", (-Ws / 2 + 10, yb + 4, zl), (-0.9, -1.0, 0.75), 50.0, "lip, back -X"),
        Closeup("lip-side", (Ws / 2, (yb + yf) / 2, zl), (1.0, 0.0, 0.12), 46.0, "lip, +X side"),
        Closeup("cut", (Ws / 2, y_cut, -25.0), (1.0, 0.15, 0.2), 60.0, "the cut, from +X"),
        Closeup(
            "back-inside",
            (0.0, y_cut - 4, -28.0),
            (0.35, 1.0, 0.5),
            95.0,
            "the back half's inside, the front half off",
            {n: Location((400.0, 0, 0)) for n in ("front", "power_board", "battery")},
        ),
        Closeup(
            "front-inside",
            (0.0, y_cut + 6, -28.0),
            (0.35, -1.0, 0.5),
            95.0,
            "the front half's inside, the back half off",
            {n: Location((400.0, 0, 0)) for n in ("back", "pi_zero_w", "screen", "usb_c_plug")},
        ),
        Closeup(
            "closing-screws",
            (xm, yb, z_low_h),
            (0.0, -1.0, 0.25),
            80.0,
            "closing screws' heads in the back face",
        ),
        Closeup("screen", (x_mod, yb, z_mc), (-0.5, -1.0, 0.35), 62.0, "screen module"),
    ]
    off_back = {n: Location((400.0, 0, 0)) for n in ("back", "pi_zero_w", "screen", "usb_c_plug")}
    closeups += [
        Closeup(
            "top-screws",
            (0.0, yb, z_t),
            (0.0, -1.0, 0.25),
            90.0,
            "top closing screws' heads in the back face",
        ),
        Closeup(
            "top-boss",
            (u_t - 2, y_cut + 3, z_t),
            (-0.4, -1.0, 0.5),
            30.0,
            "+X top boss on the front half, the back half off",
            off_back,
        ),
    ]
    if cfg.pb_stop:
        closeups.append(
            Closeup(
                "power-board-stop",
                ((xs0 + xs1) / 2, yp1 - 3, (zs0 + zs1) / 2),
                (1.0, 0.25, 0.5),
                40.0,
                "power board stop behind its +X top corner, the front half and cell off",
                {n: Location((400.0, 0, 0)) for n in ("front", "battery")},
            )
        )
    if wrap and cfg.lanyard:
        closeups.append(
            Closeup(
                "lanyard", (-Ws / 2, yl, cfg.lanyard_h), (-1.0, 0.25, 0.35), 40.0, "lanyard dip"
            )
        )
    poses = [("halves apart", {"front": apart}, ["camera"])]
    contacts = {("back", "usb_c_plug"), ("back", "front"), ("back", "screen")}
    thin_ok = [
        ("back" if (z_[2] + z_[3]) / 2 < y_cut else "front", z_, why_) for _, z_, why_ in thin_ok
    ]
    return Design(
        name="case",
        bodies=bodies,
        parts=parts,
        keepouts=ko,
        sweep=cam.sweep(0.0),
        plug_loc=plug_loc,
        port=port,
        contacts=contacts,
        hardware=hardware,
        notes=notes,
        extra_checks=extra,
        checks_off={"stability": "handheld, not made to stand"},
        thin_ok=thin_ok,
        press=press,
        closeups=closeups,
        poses=poses,
    )


def screen_free(d: Design, cfg) -> list[dict]:
    """The screen module's front frame, display and socket touch nothing printed: the gap round
    the frame at least screen_gap, round the socket the clearance. The plug and the wires sit in
    the wires' notch: nothing printed inside them, touching allowed."""
    scr = d.part("screen").shape
    out = []
    # the children hold the module's own frame; its solids, in the same order, are placed
    for kid, solid in zip(scr.children, scr.solids(), strict=True):
        name = kid.label.removeprefix("screen_")
        if name == "plate":
            continue
        gap = min(checks.distance(solid, b.solid) for b in d.bodies)
        if name in ("plug", "cable"):
            v = sum(checks.overlap(solid, b.solid) for b in d.bodies)
            out.append(
                checks.line(
                    "screen",
                    f"module {name} in the notch",
                    v <= cfg.overlap_eps,
                    f"printed overlap {v:.3f} mm3, gap {gap:.2f}",
                )
            )
            continue
        need = {"frame": cfg.screen_gap, "display": cfg.screen_gap}.get(name, cfg.clearance)
        out.append(
            checks.line(
                "screen",
                f"module {name} free",
                gap >= need - 1e-3,
                f"gap {gap:.2f}, needs {need:.2f}",
            )
        )
    return out


def wrap_lines(d: Design, cfg, ko: dict, Ws: float, yb: float, yf: float) -> list[dict]:
    """The top raised by wrap_margin still clears the keep-outs round the lip; each with the
    lowest Z the keep-out reaches over the lip ring."""
    top = d.bodies[0].solid  # the back half, or the fit test band
    for b_ in d.bodies[1:]:
        top = top + b_.solid
    raised = top.moved(Location((0, 0, cfg.wrap_margin)))
    ring = box(-Ws / 2 - 1, Ws / 2 + 1, yb - 1, yf + 1, 0.0, 60.0)
    out = []
    for name in LIP_KEEPOUTS:
        if name not in ko:
            continue
        k = ko[name]
        v = checks.overlap(raised, k)
        lo = (k & ring).bounding_box().min.Z
        out.append(
            checks.line(
                "wrap",
                f"lip {cfg.wrap_margin:g} under the {name} keep-out",
                v <= cfg.overlap_eps,
                f"keep-out from Z {lo:.2f} over the case; raised top overlaps {v:.3f} mm3",
            )
        )
    return out


def cup_lines(cam: Camera, section, lip_h, top: float, dip, cfg) -> list[dict]:
    """The cup against the traced camera along the lip's inner edge (2 lip_r under it), the
    largest gap in each region: along the front, at each end, across the back, in the -X
    lanyard dip (Y span `dip`)."""
    model = cam.model()
    body = next(k for k in model.children if k.label == "camera_body")
    panel = next(k for k in model.children if k.label == "camera_panel")
    traced = body + panel
    wire = section.outer_wire()
    regions = {"front": [], "+X end": [], "-X end": [], "back": [], "-X lanyard dip": []}
    n = 360
    for i in range(n):
        p = wire.position_at(i / n)
        h = lip_h(p.X, p.Y) if lip_h else top
        z = h - 2 * cfg.lip_r
        gap = checks.distance(Vertex(p.X, p.Y, z), traced)
        if p.X < -30 and dip and dip[0] < p.Y < dip[1]:
            k = "-X lanyard dip"
        elif p.X > 33:
            k = "+X end"
        elif p.X < -33:
            k = "-X end"
        elif p.Y > 9:
            k = "front"
        else:
            k = "back"
        regions[k].append((gap, p.X, p.Y, z))
    out = []
    for k, v in regions.items():
        if not v:
            continue
        g, x, y, z = max(v)
        out.append(
            checks.line(
                "cup",
                f"gap to the camera, {k}",
                True,
                f"at most {g:.2f} at ({x:.1f}, {y:.1f}, {z:.1f}), "
                f"{2 * cfg.lip_r:g} under the lip top",
                info=True,
            )
        )
    return out
