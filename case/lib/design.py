"""What a design module returns: printed bodies, placed parts, and what the checks need."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from build123d import Location


@dataclass
class Hole:
    """A screw hole in a printed body: checks.screw_holes wants min_wall of plastic round it."""

    p0: tuple  # on the axis, where the hole enters the plastic
    p1: tuple  # on the axis, where it ends: the far face, a blind end or a seat
    d: float  # as cut
    what: str
    floor: float = 0.0  # plastic wanted past p1 (a seat, a blind end), outside floor_d
    floor_d: float = 0.0  # the hole that goes on past a seat; 0 for a blind end
    skip: float = 0.0  # from p0, not checked: an entry cone or an edge chamfer at the face

    def moved(self, loc: Location) -> Hole:
        def mv(p):
            v = (loc * Location(tuple(p))).position
            return (v.X, v.Y, v.Z)

        return replace(self, p0=mv(self.p0), p1=mv(self.p1))


def hole(loc: Location, p0, p1, d: float, what: str, **kw) -> Hole:
    """A Hole from points in the frame of loc."""
    return Hole(tuple(p0), tuple(p1), d, what, **kw).moved(loc)


@dataclass
class Body:
    """A printed body, in the assembled position."""

    name: str
    solid: object
    color: tuple
    print_rot: tuple = (0.0, 0.0, 0.0)  # degrees about X, then Y, then Z, for printing
    explode: tuple = (0.0, 0.0, 0.0)
    note: str = ""
    holes: list = field(default_factory=list)  # Hole, for the screw hole check
    pla: str = ""  # filament colour for the print list; empty: any


FIT_EPS = 1e-3  # mm^3: a body with less plastic than this in the fit band is left out


@dataclass
class Placed:
    """A bought part (or the camera), in the assembled position."""

    name: str
    shape: object
    explode: tuple = (0.0, 0.0, 0.0)
    role: str = "part"


@dataclass
class Closeup:
    """A close-up render: `width` mm of the scene round `target`, seen from `direction`."""

    name: str
    target: tuple
    direction: tuple
    width: float = 50.0
    label: str = ""
    moves: dict = field(default_factory=dict)  # object name: Location, applied for this view


@dataclass
class Design:
    name: str
    bodies: list[Body]
    parts: list[Placed]
    keepouts: dict
    sweep: object  # camera lifted straight up, ungrown
    plug_loc: Location | None  # the plug's frame: origin on the axis at the shoulder, +Z = axis
    port: dict  # camera USB-C port: x, y, z (bottom face), w, h, depth
    contacts: set = field(default_factory=set)  # (body, part or body) pairs that touch on purpose
    hardware: list = field(default_factory=list)  # {"item", "qty", "use"}
    notes: list = field(default_factory=list)
    thin_ok: list = field(default_factory=list)  # (body, (x0, x1, y0, y1, z0, z1), why)
    press: list = field(default_factory=list)  # (body, part, (x0, x1, y0, y1, z0, z1), why)
    insertion_exempt: tuple = ("camera", "usb_c_plug")
    extra_checks: list = field(default_factory=list)  # (name, fn(design, cfg) -> lines)
    poses: list = field(default_factory=list)  # (name, {object: Location}, [hidden]) render views
    checks_off: dict = field(default_factory=dict)  # standard check name: why it does not apply
    closeups: list = field(default_factory=list)  # Closeup render views

    def body(self, name: str) -> Body:
        return next(b for b in self.bodies if b.name == name)

    def fit_band(
        self, depth: float, span: float = 0.0, back: float = 0.0, width: float = 0.0
    ) -> Design:
        """Only the printed plastic from depth below the camera's bottom up (span tall, or to
        the top; with `back`, only that far from the case's back face; with `width`, only that
        wide round X = 0), fused into one body printed as assembled (the cut face on the bed):
        a quick print to try the fit."""
        from .geom import box

        top = -depth + span if span > 0 else 500
        y0 = min(b.solid.bounding_box().min.Y for b in self.bodies)
        y1 = y0 + back if back > 0 else 500
        x = width / 2 if width > 0 else 500
        band = box(-x, x, y0 - 1, y1, -depth, top)
        cut = [b.solid & band for b in self.bodies]
        cut = [s for s in cut if s is not None and s.volume > FIT_EPS]
        one = cut[0]
        for s in cut[1:]:
            one = one + s
        names = {b.name for b in self.bodies}
        first = self.bodies[0]
        body = Body("fit_band", one, first.color, note="printed as assembled, cut face on the bed")
        body.pla = first.pla
        what = f"to {-top:g} below it" if span > 0 else "up"
        what += f", {back:g} from the back face" if back > 0 else ""
        what += f", {width:g} wide" if width > 0 else ""
        return replace(
            self,
            bodies=[body],
            contacts={tuple("fit_band" if n in names else n for n in p) for p in self.contacts},
            press=[("fit_band" if b in names else b, *rest) for b, *rest in self.press],
            hardware=[],
            notes=[*self.notes, f"fit test: the case from {depth:g} mm below the camera {what}"],
            checks_off={**self.checks_off, "stability": "fit test band, not the whole case"},
        )

    def part(self, name: str) -> Placed:
        return next(p for p in self.parts if p.name == name)
