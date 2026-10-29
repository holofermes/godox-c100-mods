"""Design parameters: a dataclass per design with defaults, overridden with --set key=value."""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import dataclass


@dataclass
class Common:
    """Settings every design shares. Sizes in mm."""

    clearance: float = 0.3  # fit gap between a printed body and a bought part (PLA)
    min_wall: float = 1.2  # thinnest printed wall the wall check accepts
    finger: float = 4.0  # finger room around a button or the mode switch
    finger_len: float = 25.0  # how far the finger room reaches out of the face
    window_margin: float = 0.5  # grows the see-through window keep-out
    window_len: float = 40.0  # how far the window keep-out reaches out of each face
    fov_len: float = 60.0  # length of the lens field-of-view cone
    lens_fov_deg: float = 90.0  # full cone angle; the lens FOV is not measured
    mic_margin: float = 1.0  # grows the round mark (maybe the mic) on -X
    mic_len: float = 8.0  # open air the mic keep-out asks for, out of the -X face
    plug_tol_xy: float = 0.2  # plug axis to port axis in X and Y: Y slack of 2.4 in 2.8
    plug_tol_below: float = 0.8  # plug shoulder may sit this far below the camera bottom
    plug_tol_above: float = 0.1  # or this far above it
    bed_small: float = 180.0  # A1 mini
    bed_large: float = 256.0  # A1, P1, X1
    overlap_eps: float = 0.01  # mm^3 of overlap that still counts as none
    wall_samples: int = 3  # wall check rays per face are wall_samples^2
    fit_test: float = 0.0  # > 0: only the case from this far below the camera up, one body
    fit_span: float = 0.0  # > 0: the fit_test band only this tall
    fit_back: float = 0.0  # > 0: the fit_test band only this far from the back face
    fit_width: float = 0.0  # > 0: the fit_test band only this wide, round X = 0
    pla_density: float = 1.24  # g/cm3
    print_fill: float = 0.7  # share of a printed body's volume that is plastic, for masses
    tip_margin: float = 3.0  # centre of mass at least this far inside the footprint


def _convert(kind, text: str):
    if kind is bool or kind == "bool":
        if text.lower() in ("1", "true", "yes", "on"):
            return True
        if text.lower() in ("0", "false", "no", "off"):
            return False
        raise SystemExit(f"config: '{text}' is not a bool")
    if kind is int or kind == "int":
        return int(text)
    if kind is float or kind == "float":
        return float(text)
    if kind is str or kind == "str":
        return text
    raise SystemExit(f"config: cannot set a field of type {kind}")


def resolve(cls, sets: list[str]):
    """Build cls() and apply 'key=value' overrides. Unknown keys fail."""
    hints = typing.get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls)}
    values = {}
    for s in sets:
        key, sep, val = s.partition("=")
        key = key.strip()
        if not sep:
            raise SystemExit(f"config: --set needs key=value, got '{s}'")
        if key not in names:
            raise SystemExit(f"config: unknown key '{key}'. Keys: {', '.join(sorted(names))}")
        values[key] = _convert(hints[key], val.strip())
    cfg = cls(**values)
    return cfg, sorted(values)


def describe(cls) -> list[dict]:
    """Field names, defaults and the comment-free type, for the README and --help."""
    hints = typing.get_type_hints(cls)
    return [
        {"key": f.name, "default": f.default, "type": hints[f.name].__name__}
        for f in dataclasses.fields(cls)
    ]
