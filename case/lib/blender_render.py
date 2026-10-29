"""Blender script: render one build folder's assembly.glb, bodies in their own colours.

blender -b --factory-startup --python blender_render.py -- out/<design>-NN [--samples N] [--size PX]
    [--closeups]

Writes render/{front34,back34,side,top,exploded,xray}.png in the build folder, one
render/pose<N>.png per pose in render.json (moving objects moved, seen like back34), and per
close-up in render.json render/close-<name>-cam.png (with the camera) and render/close-<name>.png
(camera hidden). --closeups renders the close-ups only. Cycles on CPU with the denoiser. Runs
inside Blender's Python, not the uv project.
"""

import json
import math
import sys
import time
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

CLAY_PART = (0.80, 0.80, 0.79, 1.0)
XRAY_CASE = (0.55, 0.70, 0.85, 1.0)
BG = (0.42, 0.43, 0.45)
FLOOR = (0.36, 0.37, 0.39, 1.0)

VIEWS = {
    # name: (direction from target to camera, orthographic, exploded, look)
    "front34": ((0.75, 1.0, 0.55), False, False, "clay"),
    "back34": ((-0.75, -1.0, 0.55), False, False, "clay"),
    "side": ((1.0, 0.0, 0.0), True, False, "clay"),
    "top": ((0.0, -0.0001, 1.0), True, False, "clay"),
    "exploded": ((-0.8, -1.0, 0.5), False, True, "colour"),
    "xray": ((-0.9, -1.0, 0.45), False, False, "xray"),
}
POSE_DIR = VIEWS["back34"][0]
LENS = 50  # mm, the six views and the poses
CLOSE_LENS = 85  # mm, close-ups: less perspective at about 10 cm


def args():
    a = sys.argv[sys.argv.index("--") + 1 :]
    build = Path(a[0])
    samples = int(a[a.index("--samples") + 1]) if "--samples" in a else 64
    size = int(a[a.index("--size") + 1]) if "--size" in a else 1000
    return build, samples, size, "--closeups" in a


def material(name, rgba, alpha=1.0, rough=0.55):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = rgba
    p.inputs["Roughness"].default_value = rough
    p.inputs["Alpha"].default_value = alpha
    return m


def setup_scene(samples, size):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.cycles.max_bounces = 6
    sc.cycles.transparent_max_bounces = 16
    sc.render.resolution_x = size
    sc.render.resolution_y = size
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "Standard"
    w = bpy.data.worlds.new("bg")
    w.use_nodes = True
    bgn = w.node_tree.nodes["Background"]
    bgn.inputs["Color"].default_value = (*BG, 1.0)
    bgn.inputs["Strength"].default_value = 0.55
    sc.world = w
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    sun.data.energy = 3.2
    sun.data.angle = math.radians(12)
    sun.rotation_euler = (math.radians(40), math.radians(-25), math.radians(-30))
    sc.collection.objects.link(sun)
    fill = bpy.data.objects.new("fill", bpy.data.lights.new("fill", "SUN"))
    fill.data.energy = 0.9
    fill.data.angle = math.radians(30)
    fill.rotation_euler = (math.radians(-50), math.radians(20), math.radians(150))
    sc.collection.objects.link(fill)
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.lens = LENS
    cam.data.clip_start = 0.001
    cam.data.clip_end = 10
    sc.collection.objects.link(cam)
    sc.camera = cam
    return cam


def meshes(objs):
    out = []
    for o in objs:
        if o.type == "MESH":
            out.append(o)
        out.extend(c for c in o.children_recursive if c.type == "MESH")
    return out


def world_bbox(objs):
    lo = Vector((1e9, 1e9, 1e9))
    hi = Vector((-1e9, -1e9, -1e9))
    for o in meshes(objs):
        for c in o.bound_box:
            p = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    return lo, hi


def corners(lo, hi):
    return [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]


def aim(cam, target, direction, dist):
    d = Vector(direction).normalized()
    cam.location = target + d * dist
    up = "Y"
    cam.rotation_euler = (-d).to_track_quat("-Z", up).to_euler()


def fit(cam, lo, hi, direction, ortho, margin=0.9):
    sc = bpy.context.scene
    target = (lo + hi) / 2
    pts = corners(lo, hi)
    size = (hi - lo).length
    cam.data.type = "ORTHO" if ortho else "PERSP"
    if ortho:
        aim(cam, target, direction, size * 3)
        bpy.context.view_layer.update()
        inv = cam.matrix_world.inverted()
        loc = [inv @ p for p in pts]
        w = max(abs(p.x) for p in loc) * 2
        h = max(abs(p.y) for p in loc) * 2
        cam.data.ortho_scale = max(w, h) / margin
        return
    a, b = size * 0.3, size * 10
    for _ in range(40):
        m = (a + b) / 2
        aim(cam, target, direction, m)
        bpy.context.view_layer.update()
        ok = all(
            abs(v.x - 0.5) <= margin / 2 and abs(v.y - 0.5) <= margin / 2 and v.z > 0
            for v in (world_to_camera_view(sc, cam, p) for p in pts)
        )
        if ok:
            b = m
        else:
            a = m
    aim(cam, target, direction, b)


def closeup(cam, target, direction, width):
    """Perspective view of `width` (m) across the frame at `target`, from `direction`."""
    cam.data.type = "PERSP"
    cam.data.lens = CLOSE_LENS
    half = math.atan(cam.data.sensor_width / 2 / CLOSE_LENS)
    aim(cam, Vector(target), direction, width / 2 / math.tan(half))


def main():
    t0 = time.time()
    build, samples, size, only_close = args()
    info = json.loads((build / "render.json").read_text())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(build / "assembly.glb"))
    cam = setup_scene(samples, size)
    tops = {}
    for o in info["objects"]:
        ob = bpy.data.objects.get(o["name"])
        if ob is None:
            raise SystemExit(f"render: object {o['name']} not in assembly.glb")
        tops[o["name"]] = (ob, o)
    original = {m.name: list(m.data.materials) for m in meshes([t[0] for t in tops.values()])}
    clay_part = material("clay_part", CLAY_PART)
    xray_case = material("xray_case", XRAY_CASE, alpha=0.16, rough=0.3)
    xray_cam = material("xray_cam", CLAY_PART, alpha=0.3, rough=0.4)

    body_mats = {}
    for _ob, o in tops.values():
        if o["role"] != "body":
            continue
        col = list(o["color"])
        a = col[3] if len(col) > 3 else 1.0
        bm = material(f"body_{o['name']}", (*col[:3], 1.0), rough=0.55 if a >= 1 else 0.12)
        if a < 1:
            bsdf = bm.node_tree.nodes["Principled BSDF"]
            bsdf.inputs["Transmission Weight"].default_value = 1.0 - a * 0.5
            bsdf.inputs["IOR"].default_value = 1.45
        body_mats[o["name"]] = bm

    def look(kind):
        for ob, o in tops.values():
            for m in meshes([ob]):
                m.data.materials.clear()
                if kind in ("clay", "colour") and o["role"] == "body":
                    m.data.materials.append(body_mats[o["name"]])
                elif kind == "clay":
                    m.data.materials.append(clay_part)
                elif kind == "xray" and o["role"] == "body":
                    m.data.materials.append(xray_case)
                elif kind == "xray" and o["role"] == "camera":
                    m.data.materials.append(xray_cam)
                else:
                    for mat in original[m.name]:
                        m.data.materials.append(mat)

    def explode(on):
        for ob, o in tops.values():
            off = Vector(o["explode"]) / 1000.0 if on else Vector((0, 0, 0))
            base = ob.get("_base")
            if base is None:
                ob["_base"] = list(ob.matrix_world.translation)
                base = ob["_base"]
            mw = ob.matrix_world.copy()
            mw.translation = Vector(base) + off
            ob.matrix_world = mw
        bpy.context.view_layer.update()

    floor_mat = material("floor", FLOOR, rough=0.9)
    bpy.ops.mesh.primitive_plane_add(size=60.0)
    floor = bpy.context.active_object
    floor.name = "floor"
    floor.data.materials.append(floor_mat)

    outdir = build / "render"
    outdir.mkdir(exist_ok=True)
    objs = [t[0] for t in tops.values()]
    for name, (direction, ortho, exploded, kind) in ({} if only_close else VIEWS).items():
        t = time.time()
        explode(exploded)
        look(kind)
        lo, hi = world_bbox(objs)
        floor.location = (0, 0, lo.z - 0.0005)
        floor.hide_render = name == "top"
        fit(cam, lo, hi, direction, ortho)
        bpy.context.scene.render.filepath = str(outdir / f"{name}.png")
        bpy.ops.render.render(write_still=True)
        print(f"render: {name} {time.time() - t:.1f} s", flush=True)
    explode(False)
    look("clay")
    for i, ps in enumerate([] if only_close else info.get("poses", []), 1):
        t = time.time()
        saved, hidden = {}, []
        for name, m in ps["moves"].items():
            ob = bpy.data.objects.get(name)
            if ob is None:
                raise SystemExit(f"render: pose object {name} not in assembly.glb")
            saved[name] = ob.matrix_world.copy()
            tm = Matrix(m)
            tm.translation = tm.translation / 1000.0
            ob.matrix_world = tm @ ob.matrix_world
        for name in ps.get("hide", []):
            ob = bpy.data.objects.get(name)
            if ob is None:
                continue
            for o in [ob, *ob.children_recursive]:
                if not o.hide_render:
                    o.hide_render = True
                    hidden.append(o)
        bpy.context.view_layer.update()
        shown = [o for o in objs if not o.hide_render]
        lo, hi = world_bbox(shown)
        floor.location = (0, 0, lo.z - 0.0005)
        floor.hide_render = False
        fit(cam, lo, hi, POSE_DIR, False)
        bpy.context.scene.render.filepath = str(outdir / f"pose{i}.png")
        bpy.ops.render.render(write_still=True)
        for name, mw in saved.items():
            bpy.data.objects[name].matrix_world = mw
        for o in hidden:
            o.hide_render = False
        bpy.context.view_layer.update()
        print(f"render: pose{i} ({ps['name']}) {time.time() - t:.1f} s", flush=True)
    floor.hide_render = True
    sun = bpy.data.objects["sun"]
    sun_rot = sun.rotation_euler.copy()
    cams = []
    for ob, o in tops.values():
        if o["role"] == "camera":
            cams += [ob, *ob.children_recursive]
    for cu in info.get("closeups", []):
        t = time.time()
        saved = {}
        for name, m in cu.get("moves", {}).items():
            ob = bpy.data.objects.get(name)
            if ob is None:
                raise SystemExit(f"render: close-up object {name} not in assembly.glb")
            saved[name] = ob.matrix_world.copy()
            tm = Matrix(m)
            tm.translation = tm.translation / 1000.0
            ob.matrix_world = tm @ ob.matrix_world
        bpy.context.view_layer.update()
        closeup(cam, [v / 1000.0 for v in cu["target"]], cu["direction"], cu["width"] / 1000.0)
        # key light over the viewer's left shoulder, so the faces seen are lit
        d = Vector(cu["direction"]).normalized()
        key = (Matrix.Rotation(math.radians(35), 3, "Z") @ d + Vector((0, 0, 0.6))).normalized()
        sun.rotation_euler = (-key).to_track_quat("-Z", "Y").to_euler()
        for with_cam in (True, False):
            for o in cams:
                o.hide_render = not with_cam
            tail = "-cam" if with_cam else ""
            bpy.context.scene.render.filepath = str(outdir / f"close-{cu['name']}{tail}.png")
            bpy.ops.render.render(write_still=True)
        for o in cams:
            o.hide_render = False
        for name, mw in saved.items():
            bpy.data.objects[name].matrix_world = mw
        bpy.context.view_layer.update()
        print(f"render: close-{cu['name']} {time.time() - t:.1f} s", flush=True)
    cam.data.lens = LENS
    sun.rotation_euler = sun_rot
    print(f"render: done in {time.time() - t0:.1f} s", flush=True)


main()
