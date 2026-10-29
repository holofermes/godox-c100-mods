# Notes for agents: the case

- `designs/case.py` is the design: one `Config` of settings and `build()`, which places the parts
  from `parts.json`, models both printed halves and returns a `Design` with its checks.
  `lib/` holds the shared pieces: parts loading, geometry, the camera, checks, export, renders.
- Frame: X across the camera, Y from its back to its front, Z up; Z 0 is the camera's bottom.
  The back half prints back face down, the front half front face down: a holder grows from its
  half's outer wall along Y, faces past 45 degrees need support.
- A change: `./build.sh case`, all checks pass (`exit 0`), then look at it:
  `./render.sh out/case-NN` (`--closeups` for the close-ups only). A change meant to leave the
  bodies alone leaves `back.stl` and `front.stl` byte-identical to the build before it.
- Sizes are never guessed: a part's size, pad or feature goes in `parts.json` with its source,
  `estimate: true` until measured. A missing one stops the build.
- After a change to the bodies: copy `out/case-NN/assembly.glb` and `render.json` to
  `viewer/case.glb` and `viewer/case.json`, and replace the renders in `images/` that changed.
  After a change to the wiring: `uv run python wiring.py` and the table in `README.md`.
- Lint: `uv run ruff format . && uv run ruff check .`
