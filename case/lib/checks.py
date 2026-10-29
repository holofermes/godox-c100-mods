"""Checks. Each returns lines {check, name, pass, detail}; any failing line fails the build."""

from __future__ import annotations

import math

from build123d import Axis, Compound, Location, Vector
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Dir, gp_Lin, gp_Pnt, gp_Pnt2d
from OCP.GProp import GProp_GProps
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.TopAbs import TopAbs_IN, TopAbs_REVERSED

from .design import Design, Hole
from .geom import box, cyl


def line(check: str, name: str, ok: bool, detail: str, **extra) -> dict:
    return {"check": check, "name": name, "pass": bool(ok), "detail": detail, **extra}


def _wrapped(s):
    return s.wrapped if hasattr(s, "wrapped") else s


def volume(s) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(_wrapped(s), props)
    return abs(props.Mass())


def overlap(a, b) -> float:
    op = BRepAlgoAPI_Common(_wrapped(a), _wrapped(b))
    op.SetFuzzyValue(1e-5)
    op.Build()
    if not op.IsDone():
        raise RuntimeError("boolean common failed")
    return volume(op.Shape())


def distance(a, b) -> float:
    d = BRepExtrema_DistShapeShape(_wrapped(a), _wrapped(b))
    d.Perform()
    if not d.IsDone():
        raise RuntimeError("distance failed")
    return d.Value()


def bb(s):
    bb = s.bounding_box()
    return (bb.min.X, bb.min.Y, bb.min.Z), (bb.max.X, bb.max.Y, bb.max.Z)


def bb_gap(a, b) -> float:
    (al, ah), (bl, bh) = a, b
    gaps = [max(bl[i] - ah[i], al[i] - bh[i], 0.0) for i in range(3)]
    return math.sqrt(sum(g * g for g in gaps))


def interference(d: Design, clearance: float, eps: float) -> list[dict]:
    """No printed body overlaps a part; bodies stay `clearance` away unless the pair touches on purpose.
    Zones where a body presses on a part (Design.press) are left out of it and shown as info
    lines with their own overlap."""
    out = []
    pbb = {p.name: bb(p.shape) for p in d.parts}
    for b in d.bodies:
        bbb = bb(b.solid)
        apart = []
        for p in d.parts:
            if bb_gap(bbb, pbb[p.name]) > clearance + 0.5:
                apart.append(p.name)
                continue
            name = f"{b.name} vs {p.name}"
            zones = [(box(*z), why) for bn, pn, z, why in d.press if (bn, pn) == (b.name, p.name)]
            shape = p.shape
            if zones:
                # each solid on its own: a compound's overlapping solids cut together keep pieces
                kept = []
                for s in p.shape.solids():
                    for z, _ in zones:
                        s = s - z
                    kept += s.solids()
                shape = Compound(kept)
            for z, why in zones:
                v = sum(overlap(b.solid, s) for s in p.shape.solids() for s in [s & z] if s)
                out.append(
                    line("interference", f"{name}, {why}", True, f"presses {v:.3f} mm3", info=True)
                )
            v = overlap(b.solid, shape)
            if v > eps:
                out.append(line("interference", name, False, f"overlap {v:.3f} mm3"))
                continue
            gap = distance(b.solid, shape)
            if (b.name, p.name) in d.contacts:
                out.append(line("interference", name, True, f"touches on purpose, gap {gap:.2f}"))
            else:
                ok = gap >= clearance - 1e-3
                out.append(line("interference", name, ok, f"gap {gap:.2f}, needs {clearance:.2f}"))
        if apart:
            out.append(line("interference", f"{b.name} vs far parts", True, ", ".join(apart)))
    return out


def parts_overlap(d: Design, eps: float) -> list[dict]:
    """Bought parts do not overlap each other (the plug sits in the camera's port cavity)."""
    out = []
    ps = d.parts
    bbs = {p.name: bb(p.shape) for p in ps}
    for i, a in enumerate(ps):
        for b in ps[i + 1 :]:
            if bb_gap(bbs[a.name], bbs[b.name]) > 0.0:
                continue
            v = overlap(a.shape, b.shape)
            out.append(line("parts", f"{a.name} vs {b.name}", v <= eps, f"overlap {v:.3f} mm3"))
    return out


def body_overlap(d: Design, clearance: float, eps: float) -> list[dict]:
    """Printed bodies do not overlap each other; a pair stays `clearance` apart unless it is in
    d.contacts (screwed, glued or a joint's fit: it touches on purpose)."""
    out = []
    bbs = {b.name: bb(b.solid) for b in d.bodies}
    for i, a in enumerate(d.bodies):
        for b in d.bodies[i + 1 :]:
            if bb_gap(bbs[a.name], bbs[b.name]) > clearance + 0.5:
                continue
            name = f"{a.name} vs {b.name}"
            v = overlap(a.solid, b.solid)
            if v > eps:
                out.append(line("bodies", name, False, f"overlap {v:.3f} mm3"))
                continue
            gap = distance(a.solid, b.solid)
            if (a.name, b.name) in d.contacts or (b.name, a.name) in d.contacts:
                out.append(line("bodies", name, True, f"touches on purpose, gap {gap:.2f}"))
            else:
                ok = gap >= clearance - 1e-3
                out.append(line("bodies", name, ok, f"gap {gap:.2f}, needs {clearance:.2f}"))
    if not out:
        out.append(line("bodies", "printed bodies", True, "all apart"))
    return out


def hole_ring(h: Hole, wall: float):
    """Where a hole wants plastic: a ring `wall` thick round it from h.skip to p1, and past p1
    a disc (or a ring outside floor_d) h.floor long."""
    a, b = Vector(*h.p0), Vector(*h.p1)
    u = (b - a).normalized()
    r, R = h.d / 2 + 0.02, h.d / 2 + wall
    s = a + u * h.skip
    ring = cyl(R, s, b) - cyl(r, s - u, b + u)
    if h.floor > 0:
        f = cyl(R, b, b + u * h.floor)
        if h.floor_d > 0:
            f -= cyl(h.floor_d / 2 + 0.02, b - u, b + u * (h.floor + 1))
        ring += f
    return ring


def screw_holes(d: Design, cfg) -> list[dict]:
    """min_wall of plastic round every screw hole a body declares (Body.holes), and under every
    seat and blind end that asks for it."""
    out = []
    for b in d.bodies:
        if not b.holes:
            continue
        bad = []
        for h in b.holes:
            free = hole_ring(h, cfg.min_wall) - b.solid
            v = volume(free)
            if v > cfg.overlap_eps:
                c = free.bounding_box().center()
                bad.append(f"{h.what} at ({c.X:.1f}, {c.Y:.1f}, {c.Z:.1f}): {v:.2f} mm3 short")
        detail = "; ".join(bad) if bad else f"{len(b.holes)} holes, {cfg.min_wall} round each"
        out.append(line("screws", f"{b.name} screw holes", not bad, detail))
    return out


def access(d: Design, eps: float) -> list[dict]:
    """Keep-out volumes on the camera stay empty of printed bodies."""
    out = []
    for name, ko in d.keepouts.items():
        bad = []
        for b in d.bodies:
            v = overlap(b.solid, ko)
            if v > eps:
                bad.append(f"{b.name} {v:.2f} mm3")
        out.append(line("access", name, not bad, "; ".join(bad) if bad else "clear"))
    return out


def parts_clear(check: str, name: str, solid, skip: tuple = ()):
    """A design check (for `Design.extra_checks`): no bought part but those in skip enters solid."""

    def fn(d: Design, cfg) -> list[dict]:
        bad = []
        for p in d.parts:
            if p.name in skip:
                continue
            v = overlap(p.shape, solid)
            if v > cfg.overlap_eps:
                bad.append(f"{p.name} {v:.2f} mm3")
        return [line(check, name, not bad, "; ".join(bad) if bad else "clear")]

    return fn


def insertion(d: Design, cfg) -> list[dict]:
    """The camera lifts straight up without hitting anything; the plug meets the port."""
    out = []
    bad = []
    for b in d.bodies:
        v = overlap(b.solid, d.sweep)
        if v > cfg.overlap_eps:
            bad.append(f"{b.name} {v:.2f} mm3")
    for p in d.parts:
        if p.name in d.insertion_exempt:
            continue
        v = overlap(p.shape, d.sweep)
        if v > cfg.overlap_eps:
            bad.append(f"{p.name} {v:.2f} mm3")
    out.append(
        line("insertion", "camera path along -Z", not bad, "; ".join(bad) if bad else "clear")
    )
    if d.plug_loc is None:
        out.append(line("insertion", "usb-c plug alignment", False, "design has no plug"))
        return out
    loc: Location = d.plug_loc
    o = Vector(*loc.position)
    axis = (loc * Location((0, 0, 1))).position - Vector(*loc.position)
    port = d.port
    dx, dy = o.X - port["x"], o.Y - port["y"]
    dz = o.Z - port["z"]
    ang = math.degrees(math.acos(max(-1.0, min(1.0, axis.normalized().Z))))
    ok = (
        abs(dx) <= cfg.plug_tol_xy
        and abs(dy) <= cfg.plug_tol_xy
        and -cfg.plug_tol_below <= dz <= cfg.plug_tol_above
        and ang <= 0.5
    )
    out.append(
        line(
            "insertion",
            "usb-c plug alignment",
            ok,
            f"dx {dx:+.2f} dy {dy:+.2f} (tol {cfg.plug_tol_xy}), shoulder dz {dz:+.2f} "
            f"(tol -{cfg.plug_tol_below}/+{cfg.plug_tol_above}), tilt {ang:.2f} deg (tol 0.5)",
        )
    )
    return out


def oriented(b) -> object:
    s = b.solid
    for axis, a in zip((Axis.X, Axis.Y, Axis.Z), b.print_rot, strict=True):
        if a:
            s = s.rotate(axis, a)
    bb = s.bounding_box()
    return s.moved(Location((-(bb.min.X + bb.max.X) / 2, -(bb.min.Y + bb.max.Y) / 2, -bb.min.Z)))


def bed_fit(d: Design, cfg) -> list[dict]:
    out = []
    for b in d.bodies:
        s = oriented(b)
        bb = s.bounding_box()
        size = (bb.size.X, bb.size.Y, bb.size.Z)
        txt = " x ".join(f"{v:.1f}" for v in size)
        for bed, label in ((cfg.bed_small, "A1 mini"), (cfg.bed_large, "256 bed")):
            ok = all(v <= bed for v in size)
            out.append(line("bed", f"{b.name} on {label} ({bed:.0f})", ok, f"{txt} mm as printed"))
    return out


def _face_samples(face, n: int):
    surf = BRepAdaptor_Surface(face)
    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
    rev = face.Orientation() == TopAbs_REVERSED
    fr = [(i + 0.5) / n for i in range(n)]
    for a in fr:
        for c in fr:
            u = umin + (umax - umin) * a
            v = vmin + (vmax - vmin) * c
            if BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), 1e-6).State() != TopAbs_IN:
                continue
            props = BRepLProp_SLProps(surf, u, v, 1, 1e-6)
            if not props.IsNormalDefined():
                continue
            nrm = props.Normal()
            if rev:
                nrm.Reverse()
            yield props.Value(), nrm


def min_wall(solid, samples: int, floor: float = 0.05, z0: float = -math.inf, skip=()):
    """Thinnest wall found by shooting rays inward from sample points on every face (on the
    faces above z0 only, if given). Points inside a `skip` box (x0, x1, y0, y1, z0, z1) count
    for that box instead: returns the thinnest outside them and the thinnest inside each."""
    w = _wrapped(solid)
    ix = IntCurvesFace_ShapeIntersector()
    ix.Load(w, 1e-4)
    best = (math.inf, None)
    inside = [(math.inf, None) for _ in skip]
    for f in solid.faces():
        if f.bounding_box().max.Z < z0:
            continue
        for p, n in _face_samples(f.wrapped, samples):
            if p.Z() < z0:
                continue
            d = gp_Dir(-n.X(), -n.Y(), -n.Z())
            start = gp_Pnt(p.X() + d.X() * 1e-3, p.Y() + d.Y() * 1e-3, p.Z() + d.Z() * 1e-3)
            ix.Perform(gp_Lin(start, d), 0.0, 1e4)
            if not ix.IsDone():
                continue
            ts = [ix.WParameter(i) for i in range(1, ix.NbPnt() + 1)]
            ts = [t for t in ts if t > floor]
            if not ts:
                continue
            t = min(ts) + 1e-3
            at = (round(p.X(), 2), round(p.Y(), 2), round(p.Z(), 2))
            k = next(
                (
                    i
                    for i, (x0, x1, y0, y1, zs0, zs1) in enumerate(skip)
                    if x0 <= p.X() <= x1 and y0 <= p.Y() <= y1 and zs0 <= p.Z() <= zs1
                ),
                None,
            )
            if k is not None:
                if t < inside[k][0]:
                    inside[k] = (t, at)
            elif t < best[0]:
                best = (t, at)
    return (best, inside) if skip else best


def walls(d: Design, cfg) -> list[dict]:
    """The thinnest wall of each body, at least min_wall; zones the design accepts thinner
    (Design.thin_ok) are left out of it and shown as info lines with their own thinnest."""
    out = []
    for b in d.bodies:
        zones = [(box_, why) for name, box_, why in d.thin_ok if name == b.name]
        if zones:
            (t, where), inside = min_wall(b.solid, cfg.wall_samples, skip=[z for z, _ in zones])
        else:
            (t, where), inside = min_wall(b.solid, cfg.wall_samples), []
        out.append(
            line(
                "wall",
                f"{b.name} thinnest wall",
                t >= cfg.min_wall - 0.01,
                f"{t:.2f} mm at {where}, needs {cfg.min_wall:.2f}",
                value=round(t, 3),
            )
        )
        for (ti, wi), (_, why) in zip(inside, zones, strict=True):
            out.append(
                line(
                    "wall",
                    f"{b.name} accepted thin: {why}",
                    True,
                    f"{ti:.2f} mm at {wi}" if wi else "no thinner spot found",
                    info=True,
                )
            )
    return out


def fine_features(d: Design, body: str, z0: float, area: float = 0.5) -> list[dict]:
    """Information: the thinnest wall of a body above z0 (a fin shows as a thin one) and its
    faces under `area` mm2 (slivers, steps the booleans left)."""
    s = d.body(body).solid
    t, where = min_wall(s, 5, z0=z0)
    small = sorted(((f.area, f.center()) for f in s.faces() if f.area < area), key=lambda t: t[0])
    tiny = f"{len(small)} faces under {area:g} mm2" + (
        f", smallest {small[0][0]:.3f} at ({small[0][1].X:.1f}, {small[0][1].Y:.1f}, "
        f"{small[0][1].Z:.1f})"
        if small
        else ""
    )
    return [
        line(
            "fine",
            f"{body} thinnest wall above Z {z0:g}",
            True,
            f"{t:.2f} mm at {where}",
            info=True,
        ),
        line("fine", f"{body} small faces", True, tiny, info=True),
    ]


def overhangs(d: Design) -> list[dict]:
    """Information only: downward faces steeper than 45 degrees off the bed, as printed."""
    out = []
    for b in d.bodies:
        s = oriented(b)
        area = 0.0
        for f in s.faces():
            bb = f.bounding_box()
            if bb.max.Z < 0.05:
                continue
            n = f.normal_at(f.center())
            if n.Z < -math.cos(math.radians(45)) - 1e-3:
                area += f.area
        out.append(
            line(
                "overhang",
                f"{b.name} faces facing down past 45 deg",
                True,
                f"{area:.0f} mm2",
                info=True,
            )
        )
    return out


def centroid(s):
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(_wrapped(s), props)
    c = props.CentreOfMass()
    return Vector(c.X(), c.Y(), c.Z())


def hull(pts):
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def masses(d: Design, cfg) -> list[tuple[str, float, Vector]]:
    """(name, grams, centroid) for every body and part. Bodies: PLA at cfg.print_fill."""
    out = []
    for b in d.bodies:
        g = volume(b.solid) / 1000.0 * cfg.pla_density * cfg.print_fill
        out.append((b.name, g, centroid(b.solid)))
    for p in d.parts:
        g = getattr(p.shape, "mass_g", None)
        if g is None:
            raise RuntimeError(f"part {p.name} has no mass")
        out.append((p.name, g, centroid(p.shape)))
    return out


def stability(d: Design, cfg) -> list[dict]:
    """Centre of mass inside the footprint on a flat table, with the camera docked."""
    ms = masses(d, cfg)
    total = sum(g for _, g, _ in ms)
    com = sum((c * g for _, g, c in ms), Vector(0, 0, 0)) * (1.0 / total)
    z_min = min(b.solid.bounding_box().min.Z for b in d.bodies)
    pts = []
    for b in d.bodies:
        for f in b.solid.faces():
            bb = f.bounding_box()
            if bb.max.Z < z_min + 0.05:
                pts += [(round(v.X, 3), round(v.Y, 3)) for v in f.vertices()]
    ring = hull(pts)
    if len(ring) < 3:
        return [line("stability", "stands on a table", False, "no flat bottom found")]
    margin = math.inf
    for i in range(len(ring)):
        (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % len(ring)]
        ex, ey = x1 - x0, y1 - y0
        n = math.hypot(ex, ey)
        margin = min(margin, (ex * (com.Y - y0) - ey * (com.X - x0)) / n)
    h = com.Z - z_min
    tip = math.degrees(math.atan2(margin, h)) if margin > 0 else 0.0
    ok = margin >= cfg.tip_margin
    return [
        line(
            "stability",
            "stands on a table with the camera in",
            ok,
            f"{total:.0f} g, centre of mass at ({com.X:.1f}, {com.Y:.1f}, {com.Z:.1f}), "
            f"{margin:.1f} mm inside the footprint (needs {cfg.tip_margin}), tips past {tip:.0f} deg",
            value=round(margin, 2),
        )
    ]


def run(d: Design, cfg, log=print) -> list[dict]:
    """The standard checks, then the design's own (`Design.extra_checks`)."""
    lines: list[dict] = []
    steps = [
        ("interference", lambda: interference(d, cfg.clearance, cfg.overlap_eps)),
        ("parts", lambda: parts_overlap(d, cfg.overlap_eps)),
        ("bodies", lambda: body_overlap(d, cfg.clearance, cfg.overlap_eps)),
        ("access", lambda: access(d, cfg.overlap_eps)),
        ("insertion", lambda: insertion(d, cfg)),
        ("bed", lambda: bed_fit(d, cfg)),
        ("wall", lambda: walls(d, cfg)),
        ("screws", lambda: screw_holes(d, cfg)),
        ("stability", lambda: stability(d, cfg)),
        ("overhang", lambda: overhangs(d)),
    ]
    steps += [(name, lambda fn=fn: fn(d, cfg)) for name, fn in d.extra_checks]
    for name, fn in steps:
        if name in d.checks_off:
            got = [line(name, "off", True, d.checks_off[name], info=True, off=True)]
        else:
            try:
                got = fn()
            except Exception as e:  # a check that cannot run is a failed check
                got = [line(name, "check ran", False, f"{type(e).__name__}: {e}")]
        for ln in got:
            mark = "INFO" if ln.get("info") else ("PASS" if ln["pass"] else "FAIL")
            log(f"{mark}  {ln['check']:<12} {ln['name']}: {ln['detail']}")
        lines.extend(got)
    return lines
