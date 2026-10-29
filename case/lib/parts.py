"""Part data: parts.json, {part_id: {name, src, estimate, size, features}}.

A design asks for a part and the features it needs, under `features` or as a top-level object
(like the camera's `body`). A part without a full size, or a feature nobody lists, stops the
build. What was read is saved with every build.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

CASE = Path(__file__).resolve().parent.parent


def _load(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    data = json.loads(path.read_text())
    if isinstance(data, list):
        data = {p.get("id") or p["name"]: p for p in data}
    return {k: v for k, v in data.items() if isinstance(v, dict) and not k.startswith("_")}


def _size_ok(entry: dict) -> bool:
    size = entry.get("size")
    return (
        isinstance(size, list) and len(size) == 3 and all(isinstance(s, int | float) for s in size)
    )


class PartsDB:
    def __init__(self, parts_path: Path):
        self.sources = [("parts.json", parts_path, _load(parts_path))]
        self.used: dict[str, dict] = {}

    def get(self, pid: str, features: list[str] | tuple[str, ...] = ()) -> dict:
        base = None
        for name, _path, data in self.sources:
            e = data.get(pid)
            if e and _size_ok(e):
                base = (name, e)
                break
        if base is None:
            raise SystemExit(f"parts: parts.json has no full size for '{pid}'")
        src_name, entry = base
        out = copy.deepcopy(entry)
        out.setdefault("features", {})
        feat_src = {}
        for f in features:
            for name, _path, data in self.sources:
                e = data.get(pid) or {}
                fe = e.get("features") or {}
                if f in fe or isinstance(e.get(f), dict):
                    out["features"][f] = copy.deepcopy(fe[f] if f in fe else e[f])
                    feat_src[f] = name
                    break
            else:
                raise SystemExit(f"parts: parts.json has no feature '{f}' of '{pid}'")
        rec = self.used.setdefault(pid, {"size": src_name, "features": {}})
        rec["size"] = src_name
        rec["features"].update(feat_src)
        rec["estimate"] = bool(out.get("estimate", True))
        rec["name"] = out.get("name", pid)
        out["_estimate"] = rec["estimate"]
        out["_src"] = out.get("src", [])
        return out

    def mass(self, pid: str) -> tuple[float, bool, str]:
        """Mass in g of a part, from its `mass_g`: (g, estimate, src)."""
        for name, _path, data in self.sources:
            e = data.get(pid) or {}
            if isinstance(e.get("mass_g"), int | float):
                rec = self.used.setdefault(pid, {"features": {}})
                rec["mass"] = name
                return (
                    float(e["mass_g"]),
                    bool(e.get("mass_estimate", True)),
                    e.get("mass_src", name),
                )
        raise SystemExit(f"parts: parts.json has no mass_g for '{pid}'")

    def report(self) -> dict:
        return {
            "sources": {n: Path(p).name if p else None for n, p, _ in self.sources},
            "parts": self.used,
        }
