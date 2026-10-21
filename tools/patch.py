# Base image (tools/base.py) + the boot-time layer in the RAM gap 0xe6968..0xe8000 + the
# feature overlay in the file padding + the hooks below. build.sh runs this;
# docs/hooks.md explains the design.
# usage: patch.py BASE.BIN GAP.BIN OVL.BIN V5.SYM OUT
#
# Every hook states and asserts its original bytes, so an unexpected base image
# fails the build.
import hashlib
import itertools
import os
import struct
import sys
import uuid
import zlib

base, gapf, ovlf, symf, dst = sys.argv[1:6]
B = 0x10200  # file = RAM + B
SIZE = 1841152
GAP, GAP_END = 0xE6968, int(os.environ.get("GAP_END", "0xe8000"), 0)
LOADLEN = 0x1002E  # u32 LE sector count: boot copies (N-1)*512 bytes to RAM 0
# The boot loader copies (N-1)*512 B with no check, but crt0 then zeroes
# 0xe8000..0x202a70, so anything above 0xe8000 is destroyed before any hook
# runs. 0x741 copies exactly up to 0xe8000 (docs/hooks.md); both the release
# and the canary ship it. Anything higher is a link-and-emulate-only build,
# never flashable.
TEST_ONLY = GAP_END > 0xE8000
LOADLEN_SHIP = 0x741
# The card updater opens this name and takes 1,836,000..1,846,000 B (0x253c8).
# A test-only build must not carry the name, and is padded with 0xFF to 2 MiB
# so the updater refuses it before it reads or erases anything, even renamed.
FW_NAME = "GP_CARDVR_UPGRADE.BIN"
TEST_PAD = 0x200000
BASE_SHA = "bfe8a65b49b258bcd2f7ba2886cb97b5c1858e5c3bc1f15038ac2730039056a7"  # tools/base.py

# Overlay (src/loader.c, src/link.ld). The updater writes the file to NOR byte for
# byte, so the file offset is the NOR offset the loader reads.
OVL_FILE = 0x100000  # loader.c OVL_FLASH; 64 KiB erase block 16, the app ends in block 15
OVL_MAGIC = 0x4C564F43  # loader.c OVL_MAGIC, "COVL"
OVL_RAM, OVL_TOP = 0xFF0000, 0x1000000  # header, then the body from 0xff0010
HDR = 0x10
PAGE = 0x100  # the loader reads whole 256 B pages
APP_END_FILE = 0xE6968 + B  # end of stock .data (.data start + size: crt0 literals at RAM 0x150 and 0x158)
RES_FILE = 0x170000  # resource directory
# FreeRTOS heap: main 0x5f290 defines one region from 0x24aac0 with size [0x5f46c] -
# 0x208ac0, so it ends at [0x5f46c] + 0x42000. Lowering the literal by 64 KB keeps
# 0xff0000..0x1000000 out of the heap for the overlay.
HEAP_LIT_RAM, HEAP_LIT_OLD = 0x5F46C, 0x00FBDFFF
HEAP_LIT_NEW = HEAP_LIT_OLD - (OVL_TOP - OVL_RAM)
assert HEAP_LIT_NEW + 0x42000 + 1 == OVL_RAM, hex(HEAP_LIT_NEW)

d = bytearray(open(base, "rb").read())
assert len(d) == SIZE, len(d)
assert hashlib.sha256(d).hexdigest() == BASE_SHA, "not the base image from tools/base.py"

gap = open(gapf, "rb").read()
ovl = open(ovlf, "rb").read()
sym = {}
for line in open(symf):
    a, _, name = line.split()
    sym[name] = int(a, 16)
veneers = [n for n in sym if "veneer" in n or n.endswith(("_from_arm", "_from_thumb"))]
assert not veneers, f"linker veneers {veneers}: a call between gap and overlay is not long_call (v5.h CROSS)"

g0, g1 = sym["__gap_start"], sym["__gap_end"]
assert g0 == GAP and g1 <= GAP_END, (hex(g0), hex(g1), hex(GAP_END))
assert len(gap) == g1 - g0, (len(gap), hex(g1 - g0))
o0, o1, oend = sym["__ovl_start"], sym["__ovl_load_end"], sym["__ovl_end"]
assert sym["__ovl_base"] == OVL_RAM and o0 == OVL_RAM + HDR, (hex(sym["__ovl_base"]), hex(o0))
assert len(ovl) == o1 - o0, (len(ovl), hex(o1 - o0))
ovl_read = (HDR + len(ovl) + PAGE - 1) // PAGE * PAGE  # bytes the loader reads, from 0xff0000
assert oend <= OVL_TOP and OVL_RAM + ovl_read <= OVL_TOP, (hex(oend), hex(ovl_read))


def branch(at, target, orig, link=False, always=False):
    """ARM B/BL to `target`, keeping the condition code of the original
    unless `always`."""
    off = (target - (at + 8)) >> 2
    assert -(1 << 23) <= off < (1 << 23), (hex(at), hex(target))
    op = 0x0B000000 if link else 0x0A000000
    cond = 0xE0000000 if always else orig & 0xF0000000
    return struct.pack("<I", cond | op | (off & 0xFFFFFF))


def bl(at, target, orig):
    return branch(at, target, orig, link=True)


# kind  b      ARM B to a stub, condition of the original kept
#       bal    ARM B to a stub, unconditional; the stub tests the condition
#       bl     ARM BL to a stub, condition of the original kept
# (RAM, original, kind, stub, why)
HOOKS = [
    (0x537B4, "0000d4e5", "b", "logcap_stub", "printf: capture the formatted line at r4"),
    (0x37FA0, "58d08de2", "b", "settings_stub", "mount epilogue: load the overlay, read C100.TXT"),
    (0x22360, "ecfbffeb", "bl", "tick_stub", "peripheral tick: seg_refresh, then v5_tick"),
    (0x1F5B0, "5f00a0e3", "b", "plug_stub", "VBUS insert: pick the personality"),
    (0x1F530, "737a02eb", "bl", "unplug_stub", "VBUS remove: tear the personality down"),
    (0x22260, "8500000a", "bal", "usb_poll_gate_stub", "tick: stock poll gate until armed, every tick after"),
    (0x2247C, "d4f3ffeb", "bl", "usb_tick_stub", "VBUS poll, then v5_usb_tick"),
    (0x1DDA4, "0300008a", "b", "long_select_stub", "Select held: bhi kept, long press"),
    (0x433F4, "0036a0e1", "b", "v5_gpio_set", "gpio_set entry: LED gate on 43/95"),
    (0x1F5B4, "7f8f00eb", "bl", "v5_get95", "USB busy read of GPIO 95"),
    (0x1F65C, "558f00eb", "bl", "v5_get95", "USB busy read of GPIO 95, state 3"),
    (0x22A80, "f0402de9", "b", "v5_sound_gate", "play_effect entry: sound gate"),
]

# Webcam fixes (src/v6.S, docs/webcam.md). None runs at boot: the aspect gate runs on a
# left/right press, the record gate on a record start, v6_size on a stream start. Not in the
# canary.
V6_HOOKS = [
    (0x2EC48, "e4c9ffeb", "bl", "v6_aspect_gate", "photo left/right: aspect step, gated while streaming"),
    (0x37104, "b5a8ffeb", "bl", "v6_aspect_gate", "video left/right: aspect step, gated while streaming"),
    (0x37134, "a9a8ffeb", "bl", "v6_aspect_gate", "video left/right: aspect step, gated while streaming"),
    (0x35898, "5c00000a", "b", "v6_rec_gate", "record start: beq kept, gated while streaming"),
    (0xF510, "be00d4e1", "bl", "v6_size", "APMode 0x106 second path: ldrh r0,[r4,#0xe] -> the committed frame size"),
]
# The extension unit's EP0 requests (src/xu.S, docs/webcam.md), in the handlers usb_uvc_init
# installs (0x52a90); the USB task runs them on a host request in webcam mode only. Each stub
# takes wIndex 0x0600 (unit 6, interface 0) and otherwise runs the displaced instruction. Not in
# the canary.
XU_HOOKS = [
    (0x507F0, "3430d4e5", "b", "xu_out", "UVC class OUT setup: ldrb r3,[r4,#0x34] -> the XU's SET_CUR checks"),
    (0x50980, "b423d4e1", "b", "xu_in", "UVC class IN setup: ldrh r2,[r4,#0x34] -> the XU's GET requests"),
    (0x52570, "010056e3", "b", "xu_data", "SET_CUR data stage: cmp r6,#1 -> the XU's line into the mailbox"),
]
# APMode 0x106's target 1920x1080 (full sensor, no crop); [0xf894] is a stock literal 0x438,
# 1080 has no ARM immediate. Then the frame sizes: the second path gets the host's frame
# (v6_size, r0 = W and r3 = H, so the second ldrh goes), the display size follows the second
# path as in APMode 0x107 (0xf320, 0xf324), and the probe accepts bFrameIndex 0..6.
V6_DATA = [
    (0x1F2E4, "2d2ea0e3", "a8279fe5", "0x106 target height: mov r2,#0x2d0 -> ldr r2,[pc,#0x7a8] (=0x438)"),
    (0x1F2F0, "053ca0e3", "783ea0e3", "0x106 target width: mov r3,#0x500 -> mov r3,#0x780"),
    (0x1F6E0, "b610d4e1", "ba11d4e1", "0x106 display width: ldrh r1,[r4,#6] -> ldrh r1,[r4,#0x1a]"),
    (0x1F6E4, "b820d4e1", "bc21d4e1", "0x106 display height: ldrh r2,[r4,#8] -> ldrh r2,[r4,#0x1c]"),
    (0x1F714, "b031d4e1", "0000a0e1", "0x106 second path: ldrh r3,[r4,#0x10] -> nop, v6_size returns H"),
    (0x629D4, "010053e3", "060053e3", "probe bFrameIndex: cmp r3,#1 -> cmp r3,#6"),
    (0x629D8, "a6ffff1a", "a6ffff8a", "probe bFrameIndex: bne 0x52678 -> bhi 0x52678, 0..6 pass"),
]

# The UVC config descriptor (src/v6.S v6_cfg). Stock's at 0xe39d0 has one MJPEG frame and the
# device descriptor right after it. v6_cfg must be stock's bytes with frame 1 at 1920x1080,
# frames 2.. inserted after frame 1, and the extension unit inserted after the last
# class-specific VideoControl descriptor and put on the chain (output terminal's source = the
# extension unit, whose source is the processing unit), and nothing else changed. Two words
# point at stock's copy: register_desc(1) in usb_uvc_init (pool 0x3ef00, loaded at 0x3edc8) and
# the L2 event that writes the alt-3 wMaxPacketSize 0x0400 at cfg+0xd4 (pool 0x52488,
# 0x52364..0x52374); both move to v6_cfg, the second by how far that field moved.
V6_CFG = 0xE39D0
V6_FRAMES = [(1920, 1080), (1280, 720), (640, 360), (320, 180), (256, 144), (128, 72)]
# the extension unit, as src/xu.h; the v6_cfg check below fails the build if they differ
XU_ID, XU_SEL = 6, 1
XU_GUID = uuid.UUID("21118ec3-89d6-4bb3-8015-7611dd2b1f87")
V6_L2 = (0x52364 + B, "1c319fe50010a0e30420a0e3d410c3e5d520c3e5", 0xD4)  # ldr r3,=cfg; strb at +0xd4, +0xd5


def descs(b):
    """(offset, length, type, subtype, (class, subclass) of the interface it belongs to)"""
    out, o, cls = [], 0, None
    while o < len(b):
        n = b[o]
        assert n >= 3 and o + n <= len(b), (hex(o), n)
        if b[o + 1] == 4:
            cls = (b[o + 5], b[o + 6])
        out.append((o, n, b[o + 1], b[o + 2], cls))
        o += n
    return out


def v6_cfg_want(img):
    n = struct.unpack_from("<H", img, V6_CFG + B + 2)[0]
    old = bytes(img[V6_CFG + B : V6_CFG + B + n])
    walk = descs(old)
    vc = [(o, s) for o, _, t, s, c in walk if t == 0x24 and c == (0x0E, 1)]  # video control
    vs = [(o, s) for o, _, t, s, c in walk if t == 0x24 and c == (0x0E, 2)]  # video streaming
    vhdr, ot, pu = ([o for o, s in vc if s == k] for k in (1, 3, 5))
    assert len(vhdr) == len(ot) == len(pu) == 1, (vhdr, ot, pu)
    vc_end = max(o + old[o] for o, _ in vc)
    assert struct.unpack_from("<H", old, vhdr[0] + 5)[0] == vc_end - vhdr[0], "VC header wTotalLength"
    assert old[ot[0] + 7] == old[pu[0] + 3] and XU_ID not in {old[o + 3] for o, _ in vc}, "chain, unit IDs"
    # bLength 26 = 24 + 1 input pin + bControlSize 1; 1 control, source the processing unit,
    # bmControls bit XU_SEL - 1, no string
    xu = bytes([26, 0x24, 0x06, XU_ID]) + XU_GUID.bytes_le + bytes([1, 1, old[pu[0] + 3], 1, 1 << (XU_SEL - 1), 0])
    hdr, fmt, frm = ([o for o, s in vs if s == k] for k in (1, 6, 7))
    assert len(hdr) == len(fmt) == len(frm) == 1 and old[frm[0] + 3] == 1, (hdr, fmt, frm)
    assert vc_end <= hdr[0], "VideoControl before VideoStreaming"
    f1 = old[frm[0] : frm[0] + old[frm[0]]]
    frames = b""
    for i, (w, h) in enumerate(V6_FRAMES):
        f = bytearray(f1)
        f[3] = i + 1
        struct.pack_into("<HH", f, 5, w, h)
        struct.pack_into("<I", f, 17, w * h * 2)  # dwMaxVideoFrameBufferSize, as stock's 1280*720*2
        frames += f
    new = bytearray(old[:vc_end] + xu + old[vc_end : frm[0]] + frames + old[frm[0] + len(f1) :])
    grow = len(frames) - len(f1)
    struct.pack_into("<H", new, 2, len(new))
    new[ot[0] + 7] = XU_ID
    struct.pack_into("<H", new, vhdr[0] + 5, vc_end - vhdr[0] + len(xu))
    new[fmt[0] + len(xu) + 4] = len(V6_FRAMES)
    struct.pack_into("<H", new, hdr[0] + len(xu) + 4, struct.unpack_from("<H", old, hdr[0] + 4)[0] + grow)
    return old, bytes(new)


def ep_size_at(b, ep):
    """offset of the last wMaxPacketSize of endpoint ep (the highest alt setting)"""
    return [o for o, _, t, _, _ in descs(b) if t == 5 and b[o + 2] == ep][-1] + 4


# CANARY=1 (docs/hooks.md): same boot-time hooks, stubs, loader and heap reservation, the
# overlay from canary/canary.c. The canary leaves the unplug pool word (G4) as in the base
# image; it only matters after a mount.
CANARY = os.environ.get("CANARY") == "1"

# Pool words and data patches, by file offset. The peripheral task stack goes to
# 4 KB (docs/hooks.md); it is used at boot before the updater, proven on the camera
# by the CANARY=1 build.
G4 = (0x3680C, "05800000", "02800000", "unplug pool: 0x8005 back to 0x8002, the patch posts the uninit")
STACK4K = (0xEAC2C, "00080000", "00100000", "peripheral task stack 0x800 -> 0x1000 B (InitTask def 0xdaa1c)")
HEAP = (
    HEAP_LIT_RAM + B,
    struct.pack("<I", HEAP_LIT_OLD).hex(),
    struct.pack("<I", HEAP_LIT_NEW).hex(),
    f"heap-size literal {HEAP_LIT_OLD:#x} -> {HEAP_LIT_NEW:#x}: heap region ends at {HEAP_LIT_NEW + 0x42000:#x}",
)
DATA = [STACK4K, HEAP] if CANARY else [G4, STACK4K, HEAP, *V6_DATA]
if not CANARY:
    HOOKS += V6_HOOKS + XU_HOOKS

# Words that become the address of a gap symbol, plus an offset. The MSC eject callback word
# (SD LUN ops +0x30): stock reaches it with blx r2 at 0x4e914. eject_stub is ARM, so bit 0
# stays clear.
WORD_HOOKS = [
    (0xE0978, "d4e20b00", "eject_stub", False, 0, "SD LUN ops +0x30 eject callback: gate, then note the handback")
]
if not CANARY:
    v6_old, v6_want = v6_cfg_want(d)
    c0 = sym["v6_cfg"]
    assert c0 % 4 == 0 and GAP <= c0 and c0 + len(v6_want) <= g1, hex(c0)
    assert gap[c0 - GAP : c0 - GAP + len(v6_want)] == v6_want, "src/v6.S v6_cfg is not stock's config + V6_FRAMES + XU"
    assert sym["xu_box"] % 4 == 0, "xu_box: 0xbc9cc reads GET_CUR data with word loads"
    assert d[V6_L2[0] : V6_L2[0] + 20].hex() == V6_L2[1] and ep_size_at(v6_old, 0x85) == V6_L2[2]
    moved = ep_size_at(v6_want, 0x85) - V6_L2[2]
    WORD_HOOKS += [
        (0x3EF00, "d0390e00", "v6_cfg", False, 0, "register_desc(1) in usb_uvc_init: config 0xe39d0 -> v6_cfg"),
        (0x52488, "d0390e00", "v6_cfg", False, moved, f"alt-3 wMaxPacketSize write at cfg+0xd4: + {moved:#x}"),
    ]
    print(
        f"v6_cfg {c0:#x}: {len(v6_want)} B, stock's {len(v6_old)} B config with {len(V6_FRAMES)} frames"
        f" and extension unit {XU_ID} {{{XU_GUID}}}"
    )

# G7, the auto-off variant covering VBUS states 1 and 2. Disassembled in the image:
# cmp r0,#0 / movne fp,#0 / strne fp,[r8] / bne 0x22390, so the counter is held while
# the state is 1 or 2.
BYTE_HOOKS = [
    (0x32280, "01", "00", "auto-off: cmp r0,#1 -> cmp r0,#0"),
    (0x32287, "03", "13", "auto-off: moveq -> movne"),
    (0x3228B, "05", "15", "auto-off: strne, covering states 1 and 2"),
    (0x3228F, "0A", "1A", "auto-off: bne 0x22390"),
]

for at, old, kind, val, why in HOOKS:
    old = bytes.fromhex(old)
    off = at + B
    assert d[off : off + len(old)] == old, (hex(at), d[off : off + len(old)].hex(), old.hex())
    assert kind in ("b", "bal", "bl"), kind
    t = sym[val]
    assert t % 2 == 0, f"{val} is not an ARM symbol"
    assert GAP <= t < g1, (val, hex(t))
    o = struct.unpack("<I", old)[0]
    new = bl(at, t, o) if kind == "bl" else branch(at, t, o, always=kind == "bal")
    assert len(new) == len(old)
    d[off : off + len(new)] = new
    print(f"RAM {at:#07x} file {off:#08x}: {old.hex()} -> {new.hex()}  {why}")

for at, old, name, thumb, add, why in WORD_HOOKS:
    old = bytes.fromhex(old)
    off = at + B
    assert d[off : off + len(old)] == old, (hex(at), d[off : off + len(old)].hex(), old.hex())
    t = sym[name]
    assert (t & 1) == thumb, f"{name}: state bit {t & 1}, want {int(thumb)}"
    assert GAP <= (t & ~1) + add < g1, (name, hex(t), add)
    new = struct.pack("<I", t + add)
    d[off : off + len(new)] = new
    print(f"RAM {at:#07x} file {off:#08x}: {old.hex()} -> {new.hex()}  {why} ({name}{f' + {add:#x}' if add else ''})")

for off, old, new, why in DATA:
    oldb, newb = bytes.fromhex(old), bytes.fromhex(new)
    assert d[off : off + len(oldb)] == oldb, (hex(off), d[off : off + len(oldb)].hex(), old)
    d[off : off + len(newb)] = newb
    print(f"file {off:#08x}: {old} -> {new}  {why}")

for off, old, new, why in BYTE_HOOKS:
    assert d[off] == int(old, 16), (hex(off), d[off : off + 1].hex(), old)
    d[off] = int(new, 16)
    print(f"file {off:#08x}: {old} -> {new}  {why}")

# boot-time layer into the gap; the gap must still be the stock zero padding
f0 = GAP + B
assert f0 == APP_END_FILE
assert set(d[f0 : GAP_END + B]) == {0}
d[f0 : f0 + len(gap)] = gap

# the overlay this gap belongs to (loader.c ovl_expect): body length and CRC-32, over the
# placeholder, so the loader refuses any other build's overlay
crc = zlib.crc32(ovl)
xa = sym["ovl_expect"]
assert g0 <= xa and xa + 8 <= g1 and xa % 4 == 0, hex(xa)
assert d[xa + B : xa + B + 8] == b"\xff" * 8, d[xa + B : xa + B + 8].hex()
d[xa + B : xa + B + 8] = struct.pack("<2I", len(ovl), crc)
print(f"RAM {xa:#07x} file {xa + B:#08x}: ovl_expect = length {len(ovl)}, crc32 {crc:08x}")

# overlay into the file padding: past the boot copy, before the resources, still zero
boot_copy_end = (LOADLEN_SHIP - 1) * 0x200 + B
of0, of1 = OVL_FILE, OVL_FILE + ovl_read
assert boot_copy_end <= of0 and of1 <= RES_FILE, (hex(of0), hex(of1))
assert set(d[of0:of1]) == {0}, "overlay area is not the stock zero padding"
hdr = struct.pack("<4I", OVL_MAGIC, len(ovl), crc, 0)
d[of0 : of0 + HDR + len(ovl)] = hdr + ovl

# load length: the whole gap, the copy the canary proves
old_len = struct.unpack_from("<I", d, LOADLEN)[0]
assert old_len == 0x736, hex(old_len)
new_len = LOADLEN_SHIP if not TEST_ONLY else max(LOADLEN_SHIP, (g1 + 0x200 + 0x1FF) // 0x200)
assert (new_len - 1) * 0x200 >= g1, hex(new_len)
assert TEST_ONLY or new_len == LOADLEN_SHIP
struct.pack_into("<I", d, LOADLEN, new_len)
print(f"load length {old_len:#x} -> {new_len:#x} sectors, RAM 0..{new_len * 0x200 - 0x200:#x}")
print(f"gap: {len(gap)} of {GAP_END - GAP} B used ({g0:#x}..{g1:#x}), {GAP_END - g1} B left")
print(
    f"overlay: {len(ovl)} B body + {oend - o1} B bss at {o0:#x}..{oend:#x}, {OVL_TOP - oend} B of the "
    f"{OVL_TOP - OVL_RAM} B reservation left; file {of0:#x}..{of0 + HDR + len(ovl):#x}, crc32 {crc:08x}"
)

# no hook may land inside the gap or the overlay, and no two hooks may overlap
regions = [(a + B, a + B + 4) for a, *_ in HOOKS]
regions += [(a + B, a + B + 4) for a, *_ in WORD_HOOKS]
regions += [(o, o + 4) for o, _, _, _ in DATA]
regions += [(o, o + 1) for o, _, _, _ in BYTE_HOOKS]
regions.sort()
for (a0, a1), (b0, _) in itertools.pairwise(regions):
    assert a1 <= b0, (hex(a0), hex(b0))
for a0, a1 in regions:
    assert a1 <= f0 or a0 >= f0 + len(gap), hex(a0)
    assert a1 <= of0 or a0 >= of1, hex(a0)

assert len(d) == SIZE
if TEST_ONLY:
    assert os.path.basename(dst).upper() != FW_NAME, f"a test-only build may not be named {FW_NAME}"
    d += b"\xff" * (TEST_PAD - SIZE)
open(dst, "wb").write(d)
print(dst, len(d), hashlib.sha256(d).hexdigest())
if TEST_ONLY:
    marker = os.path.join(os.path.dirname(dst), "DO-NOT-FLASH")
    open(marker, "w").write(
        f"gap limit {GAP_END:#x} is above 0xe8000: crt0 zeroes what is linked past it at boot\n"
        "emulator only, the camera would not boot\n"
    )
    print("*** " + marker + ": THIS IMAGE IS NOT FLASHABLE, emulator only ***")
