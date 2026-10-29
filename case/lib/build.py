"""build.sh <design> [--set key=value ...]: model, check, export to out/<design>-NN/."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
from pathlib import Path

from . import checks
from .config import describe, resolve
from .export import next_dir, write
from .parts import CASE, PartsDB


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="build.sh", description=__doc__)
    ap.add_argument("design", help="module name in designs/")
    ap.add_argument(
        "--set", action="append", default=[], metavar="KEY=VALUE", help="override a setting"
    )
    ap.add_argument(
        "--parts",
        default=os.environ.get("C100_PARTS", str(CASE / "parts.json")),
        help="parts file (env C100_PARTS), default case/parts.json",
    )
    ap.add_argument("--out", default=str(CASE / "out"), help="build folders go here")
    ap.add_argument("--keys", action="store_true", help="list the design's settings and exit")
    a = ap.parse_args(argv)

    sys.path.insert(0, str(CASE))
    mod = importlib.import_module(f"designs.{a.design}")
    if a.keys:
        for k in describe(mod.Config):
            print(f"{k['key']:<22} {k['type']:<6} {k['default']}")
        return 0
    cfg, overrides = resolve(mod.Config, a.set)
    db = PartsDB(Path(a.parts))

    t0 = time.time()
    d = mod.build(cfg, db)
    if cfg.fit_test > 0:
        d = d.fit_band(cfg.fit_test, cfg.fit_span, cfg.fit_back, cfg.fit_width)
    print(f"build: {a.design} modelled in {time.time() - t0:.1f} s")
    t1 = time.time()
    lines = checks.run(d, cfg)
    print(f"build: checks took {time.time() - t1:.1f} s")
    out = next_dir(Path(a.out), a.design)
    write(d, cfg, overrides, db.report(), lines, out, CASE, ["build.sh", *argv], db=db)
    failed = [ln for ln in lines if not ln["pass"]]
    print(f"build: wrote {out}")
    if failed:
        print(f"build: FAILED {len(failed)} of {len(lines)} checks", file=sys.stderr)
        for ln in failed:
            print(f"FAIL  {ln['check']:<12} {ln['name']}: {ln['detail']}", file=sys.stderr)
        return 1
    print(f"build: all {len(lines)} checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
