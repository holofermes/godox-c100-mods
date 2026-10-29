"""The C100 camera from parts.json: body, coloured details, keep-outs, insertion sweep.

World frame = camera frame: X at the body centre, Y = 0 on the back face, Z = 0 on
the bottom edge. The body is the intersection of three profile prisms: the X-Z outline (corner
radius), the side profile (body.side_profile) and the plan seen from below (body.plan_bottom),
with the back face's edges rounded edge_r all round and the lower body's front face edges too
(Godox's renders show the rounding run round the X-Z corners; the three prisms alone leave it square
there). The patterned panel's front edges are rounded `panel_round` (its pillow edge). The thin
part's front edge along the top corners stays square: no case reaches it.
Lifting it straight up sweeps the same shapes with their tops pulled up, so the sweep is exact
for this model. Details add their columns: a detail on the back or front face its bounding box
pulled up, a detail on a curved side (the round mark) the same box clipped to the plan outline
grown by its proud.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from build123d import Compound, Cone, Cylinder, Plane, Polygon, Vector, extrude, fillet, offset

from .geom import (
    along_x,
    along_y,
    along_z,
    box,
    face_at,
    rdp,
    rounded_block,
    rrect,
    shape2d,
)
from .parts import PartsDB

FEATURES = (
    "body",
    "window_back",
    "window_front",
    "pill",
    "shutter",
    "led",
    "lens",
    "panel",
    "mode_switch",
    "round_mark",
    "microsd",
    "usb_c",
    "reset",
)

WHITE = (0.93, 0.93, 0.91)
PANEL = (0.80, 0.80, 0.80)
GREY = (0.55, 0.56, 0.58)
GLASS = (0.62, 0.80, 0.86)
BLACK = (0.06, 0.06, 0.07)
GREEN = (0.2, 0.85, 0.35)
RECEPTACLE_DEPTH = 7.0  # USB-C receptacle shell depth behind its face, typical
SIDE_CLIP = 0.05  # a side detail stays within its proud plus this of the plan outline


@dataclass
class FaceFrame:
    origin: Vector
    x_dir: Vector
    normal: Vector


def _interp(xs, ys, x):
    i = bisect.bisect_left(xs, x)
    if i <= 0:
        return ys[0]
    if i >= len(xs):
        return ys[-1]
    t = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
    return ys[i - 1] + t * (ys[i] - ys[i - 1])


@dataclass
class Camera:
    spec: dict
    W: float = 0.0
    D: float = 0.0
    H: float = 0.0
    b: dict = field(default_factory=dict)
    f: dict = field(default_factory=dict)
    plan_pts: list = field(default_factory=list)
    mass_g: float = 0.0
    mass_estimate: bool = True

    @classmethod
    def from_db(cls, db: PartsDB) -> Camera:
        spec = db.get("camera", FEATURES)
        W, D, H = spec["size"]
        cam = cls(spec=spec, W=W, D=D, H=H, b=spec["features"]["body"], f=spec["features"])
        cam._plan()
        cam.mass_g, cam.mass_estimate, _ = db.mass("camera")
        return cam

    # profiles -----------------------------------------------------------------
    def _rows(self):
        return sorted(self.b["side_profile"], key=lambda r: r[0])

    def _side(self, col: int):
        """Side profile (u = Y, v = Z) with front column col (2 body, 3 with panel)."""
        rows = self._rows()
        y0, y1 = self.b["bottom_rounds"]["flat_strip_y"]
        front = rdp([(r[col], r[0]) for r in rows], 0.03)
        back = rdp([(r[1], r[0]) for r in reversed(rows)], 0.03)
        return Polygon(*([(y0, 0.0), (y1, 0.0)] + front + back), align=None)

    def _side_sweep(self, z_top: float):
        """Region above the side profile's lower edge, up to z_top, panel included."""
        rows = self._rows()
        y0, y1 = self.b["bottom_rounds"]["flat_strip_y"]
        lower = [r for r in rows if r[0] <= self.b["lower"]["z1"] + 0.01]
        k = max(range(len(lower)), key=lambda i: lower[i][3])
        front, run = [], -1.0
        for r in lower[: k + 1]:
            run = max(run, r[3])
            front.append((run, r[0]))
        jb = next(i for i, r in enumerate(rows) if r[1] <= 1e-6)
        back = [(r[1], r[0]) for r in reversed(rows[: jb + 1])]
        y_max = front[-1][0]
        pts = [(y0, 0.0), (y1, 0.0)] + rdp(front, 0.03) + [(y_max, z_top), (0.0, z_top)]
        pts += rdp(back, 0.03)
        return Polygon(*pts, align=None)

    def _plan(self, round_back: bool = True):
        """Plan seen from below (u = X, v = Y): the widest outline at any height. Its back
        corners rounded edge_r; square with round_back False, for shapes that round the back
        edges themselves (a near copy of the same round breaks the booleans)."""
        W, D, er = self.W, self.D, self.b["edge_r"]
        samples = sorted(self.b["plan_bottom"])
        pts = [(0.0, W / 2 - er if round_back else W / 2)]
        for y, hw in samples:
            if y < er:
                hw = max(hw, W / 2 - er + math.sqrt(er * er - (er - y) ** 2) if round_back else W)
            pts.append((y, min(hw, W / 2)))
        (ya, ha), (yb, hb) = samples[-2], samples[-1]
        pts.append((D, hb + (hb - ha) * (D - yb) / (yb - ya)))
        if round_back:
            self.plan_pts = pts
        right = [(hw, y) for y, hw in pts]
        left = [(-hw, y) for y, hw in reversed(pts)]
        return Polygon(*(right + left), align=None)

    def half_width(self, y: float) -> float:
        return _interp([p[0] for p in self.plan_pts], [p[1] for p in self.plan_pts], y)

    def front_y(self, v: float) -> float:
        rows = self._rows()
        return _interp([r[0] for r in rows], [r[3] for r in rows], v)

    def clean_lower(self, grow: float = 0.0, z_top: float | None = None):
        """The lower body from primitives, for shaping a case against: width, lower-body depth,
        the X-Z corner radius and the edge rounding on every edge of the back and front faces,
        from Z = 0 up to z_top (default the lower body's top), all grown by `grow`. No panel,
        no details. The traced model (`model`, `sweep`) stays the reference for the checks."""
        W, D, g = self.W, self.b["lower"]["y1"], grow
        R, er = self.b["corner_r_xz"], self.b["edge_r"]
        z1 = self.b["lower"]["z1"] if z_top is None else z_top
        return rounded_block(-W / 2 - g, W / 2 + g, -g, D + g, -g, z1, R + g, er + g)

    def panel_round(self) -> float:
        """Round on the patterned panel's front edges: the largest, up to the panel's proud,
        whose outline still holds the plan's panel underside (body.plan_bottom past the lower
        body's front) on both sides."""
        p, yl = self.f["panel"], self.b["lower"]["y1"]
        yp = p["y_front"]
        edges = (p["u"] + p["w"] / 2, p["w"] / 2 - p["u"])
        pts = [(y, hw) for y, hw in self.plan_pts if y > yl]
        r = p["proud"]
        while r > 0.05:
            ok = True
            for y, hw in pts:
                for e in edges:
                    need = min(hw, e)
                    dy = y - (yp - r)
                    have = e if dy <= 0 else e - r + math.sqrt(max(0.0, r * r - dy * dy))
                    ok &= have >= need - 1e-6
            if ok:
                return round(r, 2)
            r -= 0.05
        return 0.0

    def clean_panel(self, grow: float = 0.0, z_top: float | None = None):
        """The patterned panel from primitives, for shaping a case against: its outline (corner
        radius from the front view) out to its front, the front edges rounded `panel_round`,
        from inside the lower body up to z_top (the top corners left square), all grown by
        `grow`. With `clean_lower` it holds the traced body and panel."""
        p, g, yl = self.f["panel"], grow, self.b["lower"]["y1"]
        z1 = self.b["lower"]["z1"] if z_top is None else z_top
        x0, x1 = p["u"] - p["w"] / 2 - g, p["u"] + p["w"] / 2 + g
        z0 = p["v"] - p["h"] / 2 - g
        rf = self.panel_round()
        return rounded_block(x0, x1, yl - 2, p["y_front"] + g, z0, z1, p["r"] + g, 0, rf + g)

    def body_solid(self, col: int = 2):
        """The traced body; col 3 takes the panel's front, without the lower body's front
        edge rounds (it only shapes the panel)."""
        W, D, H = self.W, self.D, self.H
        r, er = self.b["corner_r_xz"], self.b["edge_r"]
        a = along_y(rrect(W, H, r, 0, H / 2), 0, D + 1)
        a = fillet(face_at(a, "Y", 0.0).edges(), er)
        s = along_x(self._side(col), -W / 2 - 1, W / 2 + 1)
        t = along_z(self._plan(round_back=False), -1, H + 1)
        body = a & s & t
        if col == 2:
            body &= self._front_rounds(0.0, H + 1)
        return body

    def _front_rounds(self, g: float, z_top: float):
        """The lower body's front face with its edges rounded edge_r, grown by g, to z_top."""
        W, r, er, yl = self.W, self.b["corner_r_xz"], self.b["edge_r"], self.b["lower"]["y1"]
        return rounded_block(-W / 2 - g, W / 2 + g, -g - 2, yl + g, -g, z_top, r + g, 0, er + g)

    def sweep(self, grow: float = 0.0, up: float = 250.0):
        """Everything the body, panel and details pass through when lifted straight up."""
        W, D, H, g = self.W, self.D, self.H, grow
        r, er = self.b["corner_r_xz"] + g, self.b["edge_r"] + g
        z_top = H + up
        side = self._side_sweep(z_top)
        plan = self._plan(round_back=False)
        if g > 0:
            side, plan = offset(side, g), offset(plan, g)
        # the back edge rounds, the lower body's front ones, and the panel's column in front
        a = rounded_block(-W / 2 - g, W / 2 + g, -g, D + g + 2, -g, z_top, r, er, 0)
        panel = self.clean_panel(g, z_top)
        s = along_x(side, -W / 2 - g - 2, W / 2 + g + 2)
        t = along_z(plan, -g - 2, z_top)
        sw = a & (self._front_rounds(g, z_top) + panel) & s & t
        for d in self.details():
            bb = d.bounding_box()
            lo, hi = bb.min, bb.max
            col = box(lo.X - g, hi.X + g, lo.Y - g, hi.Y + g, lo.Z - g, z_top)
            grow = getattr(d, "plan_grow", None)
            if grow is not None:
                col &= along_z(offset(self._plan(), grow + g), lo.Z - g - 1, z_top + 1)
            sw += col
        return sw

    # faces --------------------------------------------------------------------
    def frame(self, face: str, u: float, v: float) -> FaceFrame:
        H = self.H
        if face == "back":
            return FaceFrame(Vector(u, 0, v), Vector(1, 0, 0), Vector(0, -1, 0))
        if face == "front":
            return FaceFrame(Vector(u, self.front_y(v), v), Vector(1, 0, 0), Vector(0, 1, 0))
        if face == "right":
            return FaceFrame(Vector(self.half_width(u), u, v), Vector(0, 1, 0), Vector(1, 0, 0))
        if face == "left":
            return FaceFrame(Vector(-self.half_width(u), u, v), Vector(0, 1, 0), Vector(-1, 0, 0))
        if face == "bottom":
            return FaceFrame(Vector(u, v, 0), Vector(1, 0, 0), Vector(0, 0, -1))
        if face == "top":
            return FaceFrame(Vector(u, v, H), Vector(1, 0, 0), Vector(0, 0, 1))
        raise SystemExit(f"camera: unknown face '{face}'")

    def face_prism(self, face, u, v, shape, w, h, r, grow, a0, a1):
        """Prism of a face shape, grown by `grow`, from a0 to a1 along the outward normal."""
        fr = self.frame(face, u, v)
        sk = shape2d(shape, w, h, 0, 0, r, grow)
        pl = Plane(origin=fr.origin + fr.normal * a0, x_dir=fr.x_dir, z_dir=fr.normal)
        return extrude(pl * sk, amount=a1 - a0)

    def prism(self, name: str, grow: float, a0: float, a1: float):
        ft = self.f[name]
        if "d" in ft:
            d = ft["d"]
            return self.face_prism(ft["face"], ft["u"], ft["v"], "circle", d, d, 0, grow, a0, a1)
        shape = "pill" if name in ("pill", "usb_c") else "rect"
        return self.face_prism(
            ft["face"], ft["u"], ft["v"], shape, ft["w"], ft["h"], ft.get("r", 0.0), grow, a0, a1
        )

    def switch_prism(self, grow: float, a0: float, a1: float):
        sw = self.f["mode_switch"]
        g = grow + sw.get("frame_grow", 0.0)
        return self.face_prism(
            "right", sw["u"], sw["v"], "pill", sw["slot_w"], sw["slot_len"], 0, g, a0, a1
        )

    # model --------------------------------------------------------------------
    def details(self):
        f = self.f
        shutter = self.prism("shutter", 0.0, -0.3, 0.1)
        shutter.label, shutter.color = "camera_shutter", GREY
        pill = self.prism("pill", 0.0, -0.3, f["pill"].get("proud", 0.0))
        pill.label, pill.color = "camera_pill", GREY
        led = self.prism("led", 0.0, -0.3, 0.1)
        led.label, led.color = "camera_led", GREEN
        sw = f["mode_switch"]
        x = self.half_width(sw["u"])
        knob_v = min(sw.get("positions_v", {"off": sw["v"]}).values())
        knob_pl = Plane(origin=(x - 0.5, sw["u"], knob_v), x_dir=(0, 1, 0), z_dir=(1, 0, 0))
        knob = knob_pl * Cylinder(sw["knob_d"] / 2, sw["knob_proud"] + 0.5, align=None)
        knob.label, knob.color = "camera_knob", GREY
        lens = self.prism("lens", 0.0, -0.3, 0.1)
        lens.label, lens.color = "camera_lens", BLACK
        mark = self._clip_side(self.prism("round_mark", 0.0, -0.6, 0.1), 0.1)
        mark.label, mark.color = "camera_round_mark", GREY
        return [shutter, pill, led, knob, lens, mark]

    def _clip_side(self, shape, proud: float):
        """A flat prism on a curved side face sticks out of the body past its middle; keep it
        within its proud of the plan outline. The sweep clips its column the same way."""
        grow = proud + SIDE_CLIP
        out = shape & along_z(offset(self._plan(), grow), -1, self.H + 1)
        out.plan_grow = grow
        return out

    def model(self) -> Compound:
        f, seam = self.f, self.b["seam_y"]
        body = self.body_solid(2)
        p = f["panel"]
        panel = along_y(
            rrect(p["w"], p["h"], p["r"], p["u"], p["v"]), self.b["lower"]["y1"] - 1.0, p["y_front"]
        )
        panel = fillet(face_at(panel, "Y", p["y_front"]).edges(), self.panel_round())
        panel = panel & self.body_solid(3)
        body -= self.prism("window_back", 0.0, -seam, 1.0)
        body -= self.prism("window_front", 0.0, -(self.front_y(f["window_front"]["v"]) - seam), 1.0)
        port = self.port()
        body -= self.prism("usb_c", 0.0, -port["depth"], 1.0)
        body -= self.prism("microsd", 0.0, -2.0, 1.0)
        body -= self.prism("reset", 0.0, -2.0, 1.0)
        body -= self.switch_prism(0.0, -1.0, 1.0)
        wb, wf = f["window_back"], f["window_front"]
        glass = along_y(
            rrect(max(wb["w"], wf["w"]), max(wb["h"], wf["h"]), wb.get("r", 0), wb["u"], wb["v"]),
            seam - 0.8,
            seam + 0.8,
        )
        body.label, body.color = "camera_body", WHITE
        panel.label, panel.color = "camera_panel", PANEL
        glass.label, glass.color = "camera_window", GLASS
        comp = Compound(children=[body, panel, glass, *self.details()], label="camera")
        comp.pid, comp.mass_g = "camera", self.mass_g
        comp.estimate = bool(self.spec.get("_estimate", True))
        comp.src = self.spec.get("_src", [])
        return comp

    # keep-outs ----------------------------------------------------------------
    def keepouts(self, cfg):
        """Volumes the case must not enter, keyed by name."""
        ko = {
            "window_back": self.prism("window_back", cfg.window_margin, -0.5, cfg.window_len),
            "window_front": self.prism("window_front", cfg.window_margin, -0.5, cfg.window_len),
            "shutter": self.prism("shutter", cfg.finger, -0.5, cfg.finger_len),
            "pill": self.prism("pill", cfg.finger, -0.5, cfg.finger_len),
            "mode_switch": self.switch_prism(cfg.finger, -2.0, cfg.finger_len),
            "mic": self.prism("round_mark", cfg.mic_margin, -2.0, cfg.mic_len),
        }
        lens = self.f["lens"]
        fr = self.frame(lens["face"], lens["u"], lens["v"])
        half = math.radians(cfg.lens_fov_deg / 2)
        r0 = lens["d"] / 2
        r1 = r0 + cfg.fov_len * math.tan(half)
        pl = Plane(origin=fr.origin, x_dir=fr.x_dir, z_dir=fr.normal)
        ko["lens_fov"] = pl * Cone(r0, r1, cfg.fov_len, align=None)
        return ko

    def port(self) -> dict:
        p = self.f["usb_c"]
        recess = p.get("recess_plan") or 0.0
        depth = p.get("depth") or (recess + RECEPTACLE_DEPTH)
        return {
            "x": p["u"],
            "y": p["v"],
            "z": 0.0,
            "w": p["w"],
            "h": p["h"],
            "depth": depth,
            "recess_max": recess,
        }
