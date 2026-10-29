"""Small geometry helpers on top of build123d. Millimetres, world X right, Y back to front, Z up."""

from __future__ import annotations

import math

from build123d import (
    Axis,
    Box,
    Circle,
    Cylinder,
    Location,
    Plane,
    Polygon,
    Pos,
    Rectangle,
    RectangleRounded,
    SlotOverall,
    Vector,
    extrude,
    fillet,
)


def along_x(sketch, x0: float, x1: float):
    """Extrude a sketch drawn in (u=Y, v=Z) from X = x0 to x1."""
    return extrude(Plane.YZ.offset(x0) * sketch, amount=x1 - x0)


def along_y(sketch, y0: float, y1: float):
    """Extrude a sketch drawn in (u=X, v=Z) from Y = y0 to y1."""
    return extrude(Plane.XZ.offset(-y1) * sketch, amount=y1 - y0)


def along_z(sketch, z0: float, z1: float):
    """Extrude a sketch drawn in (u=X, v=Y) from Z = z0 to z1."""
    return extrude(Plane.XY.offset(z0) * sketch, amount=z1 - z0)


def rrect(w: float, h: float, r: float, cu: float = 0.0, cv: float = 0.0):
    """Rounded rectangle sketch centred at (cu, cv). r is clamped to fit."""
    r = min(r, w / 2 - 1e-3, h / 2 - 1e-3)
    if r <= 0.01:
        return Pos(cu, cv) * Rectangle(w, h)
    return Pos(cu, cv) * RectangleRounded(w, h, r)


def shape2d(
    shape: str, w: float, h: float, cu: float, cv: float, r: float = 0.0, grow: float = 0.0
):
    """Sketch for a face feature: rect, pill or circle (w is the diameter), grown by `grow`."""
    w2, h2 = w + 2 * grow, h + 2 * grow
    if shape == "circle":
        return Pos(cu, cv) * Circle(w2 / 2)
    if shape == "pill":
        if h2 > w2:
            return Pos(cu, cv) * SlotOverall(h2, w2, rotation=90)
        return Pos(cu, cv) * SlotOverall(w2, h2)
    return rrect(w2, h2, r + grow, cu, cv)


def poly_rounded(pts: list[tuple[float, float]], radii: list[float]):
    """Closed polygon sketch with a fillet radius per vertex (0 for sharp)."""
    face = Polygon(*pts, align=None)
    for (u, v), r in zip(pts, radii, strict=True):
        if r <= 0:
            continue
        vs = [x for x in face.vertices() if (Vector(x.center()) - Vector(u, v, 0)).length < 1e-6]
        if vs:
            face = fillet(vs, r)
    return face


def rdp(pts, tol: float):
    """Drop points closer than tol to the line through their neighbours (Ramer-Douglas-Peucker)."""
    if len(pts) < 3:
        return list(pts)
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy) or 1e-12
    dists = [abs(dy * (x - x0) - dx * (y - y0)) / n for x, y in pts[1:-1]]
    i = max(range(len(dists)), key=dists.__getitem__)
    if dists[i] <= tol:
        return [pts[0], pts[-1]]
    left = rdp(pts[: i + 2], tol)
    right = rdp(pts[i + 1 :], tol)
    return left[:-1] + right


def rect4(x0, x1, y0, y1, radii):
    """Rectangle sketch with its own radius per corner: (x0,y0), (x1,y0), (x1,y1), (x0,y1)."""
    return poly_rounded([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], list(radii))


def box(x0, x1, y0, y1, z0, z1):
    """Axis aligned box from corner ranges."""
    b = Box(x1 - x0, y1 - y0, z1 - z0)
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * b


def cyl(r: float, p0, p1):
    """Cylinder of radius r from point p0 to point p1."""
    a, b = Vector(*p0), Vector(*p1)
    d = b - a
    plane = Plane(origin=a, z_dir=d.normalized())
    return plane * Cylinder(r, d.length, align=None)


def _flat(bb, axis: str, v: float) -> bool:
    lo, hi = getattr(bb.min, axis), getattr(bb.max, axis)
    return abs(lo - v) < 1e-6 and abs(hi - v) < 1e-6


def rounded_block(x0, x1, y0, y1, z0, z1, r_xz: float, r_back: float, r_front: float | None = None):
    """The camera body's form: a box whose two bottom corners seen along Y are rounded r_xz and
    whose back face (Y = y0) has every edge but the top one rounded r_back, the front face
    (Y = y1) r_front (default r_back). Grown by g with every radius + g it stays concentric."""
    r_front = r_back if r_front is None else r_front
    xz = poly_rounded([(x0, z0), (x1, z0), (x1, z1), (x0, z1)], [r_xz, r_xz, 0, 0])
    s = along_y(xz, y0, y1)
    for y, r in ((y0, r_back), (y1, r_front)):
        if r > 0:
            es = [
                e
                for e in s.edges()
                if _flat(e.bounding_box(), "Y", y) and not _flat(e.bounding_box(), "Z", z1)
            ]
            s = fillet(es, r)
    return s


def in_rounded_block(p, x0, x1, y0, y1, z0, r_xz, r_back, r_front=None) -> float:
    """How far point p is inside a `rounded_block` (open at the top): negative outside. Exact
    for the rolling-ball edge rounds, which shrink the X-Z profile near the Y faces."""
    r_front = r_back if r_front is None else r_front
    x, y, z = p
    shrink = 0.0
    for dy, r in ((y - y0, r_back), (y1 - y, r_front)):
        if dy < 0:
            return dy
        if dy < r:
            shrink = max(shrink, r - math.sqrt(r * r - (r - dy) ** 2))
    xc, hw = (x0 + x1) / 2, (x1 - x0) / 2 - shrink
    R, zb = r_xz - shrink, z0 + shrink
    ax = abs(x - xc)
    d = min(hw - ax, z - zb)
    if ax > hw - R and z < zb + R:
        d = min(d, R - math.hypot(ax - (hw - R), z - (zb + R)))
    return d


def face_at(shape, axis: str, v: float):
    """The planar face of shape lying in the plane axis = v (one face expected)."""
    fs = [f for f in shape.faces() if _flat(f.bounding_box(), axis, v)]
    if len(fs) != 1:
        raise SystemExit(f"geom: {len(fs)} faces at {axis} = {v:.3f}, expected one")
    return fs[0]


def rbox_z(x0, x1, y0, y1, z0, z1, r):
    """Box with vertical edges rounded by r."""
    return along_z(rrect(x1 - x0, y1 - y0, r, (x0 + x1) / 2, (y0 + y1) / 2), z0, z1)


def bbox(shape):
    bb = shape.bounding_box()
    return [bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]


def frame_loc(origin, x_dir, z_dir) -> Location:
    """Location that maps a part's local frame to world: local X -> x_dir, local Z -> z_dir."""
    return Location(Plane(origin=origin, x_dir=x_dir, z_dir=z_dir))


__all__ = [
    "Axis",
    "along_x",
    "along_y",
    "along_z",
    "bbox",
    "box",
    "cyl",
    "frame_loc",
    "poly_rounded",
    "rbox_z",
    "rdp",
    "rect4",
    "face_at",
    "in_rounded_block",
    "rounded_block",
    "rrect",
    "shape2d",
]
