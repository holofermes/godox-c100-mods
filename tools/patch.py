# Base image (tools/base.py) + C payload in the RAM gap 0xe6828..0xe8000 + the hooks
# below. build.sh runs this; docs/hooks.md explains the design.
# usage: patch.py BASE.BIN V5.BIN V5.SYM OUT
#
# Every hook states and asserts its original bytes, so an unexpected base image
# fails the build.
import hashlib
import itertools
import os
import struct
import sys

base, binf, symf, dst = sys.argv[1:5]
B = 0x10200  # file = RAM + B
SIZE = 1841152
GAP, GAP_END = 0xE6828, int(os.environ.get("GAP_END", "0xe8000"), 0)
LOADLEN = 0x1002E  # u32 LE sector count: boot copies (N-1)*512 bytes to RAM 0
# The boot loader copies (N-1)*512 B with no check, but crt0 then zeroes
# 0xe8000..0x202a70, so a payload above 0xe8000 is destroyed before any hook
# runs. 0x741 is the real ceiling (docs/hooks.md); anything higher
# is a link-and-emulate-only build, never flashable.
TEST_ONLY = GAP_END > 0xE8000
LOADLEN_MAX = 0x100000 if TEST_ONLY else 0x741
# The card updater opens this name and takes 1,836,000..1,846,000 B (0x252b0).
# A test-only build must not carry the name, and is padded with 0xFF to 2 MiB
# so the updater refuses it before it reads or erases anything, even renamed.
FW_NAME = "GP_CARDVR_UPGRADE.BIN"
TEST_PAD = 0x200000
BASE_SHA = "d078d5b0feaa29f72c213e038aeade61a261d9c78ce6982d96c5ed2453481977"  # tools/base.py

d = bytearray(open(base, "rb").read())
assert len(d) == SIZE, len(d)
assert hashlib.sha256(d).hexdigest() == BASE_SHA, "not the base image from tools/base.py"

code = open(binf, "rb").read()
sym = {}
for line in open(symf):
    a, _, name = line.split()
    sym[name] = int(a, 16)
end = sym["__v5_end"]
assert GAP <= end <= GAP_END, (hex(end), hex(GAP_END))
assert len(code) <= end - GAP, (len(code), hex(end - GAP))


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
    (0x535E0, "0000d4e5", "b", "logcap_stub", "printf: capture the formatted line at r4"),
    (0x37DF4, "58d08de2", "b", "settings_stub", "mount epilogue: read C100.TXT"),
    (0x22238, "04fcffeb", "bl", "tick_stub", "peripheral tick: seg_refresh, then v5_tick"),
    (0x1F540, "5f00a0e3", "b", "plug_stub", "VBUS insert: pick the personality"),
    (0x1F4C0, "667a02eb", "bl", "unplug_stub", "VBUS remove: tear the personality down"),
    (0x22138, "8500000a", "bal", "usb_poll_gate_stub", "tick: stock poll gate until armed, every tick after"),
    (0x22354, "02f4ffeb", "bl", "usb_tick_stub", "VBUS poll, then v5_usb_tick"),
    (0x1DDA4, "0300008a", "b", "long_select_stub", "Select held: bhi kept, long press"),
    (0x43224, "0036a0e1", "b", "v5_gpio_set", "gpio_set entry: LED gate on 43/95"),
    (0x1F544, "278f00eb", "bl", "v5_get95", "USB busy read of GPIO 95"),
    (0x1F5EC, "fd8e00eb", "bl", "v5_get95", "USB busy read of GPIO 95, state 3"),
    (0x22958, "f0402de9", "b", "v5_sound_gate", "play_effect entry: sound gate"),
]

# Webcam gates (src/v6.S, docs/webcam.md). Neither runs at boot: the aspect gate runs on a
# left/right press, the record gate on a record start. Not in the canary.
V6_HOOKS = [
    (0x2EA90, "20caffeb", "bl", "v6_aspect_gate", "photo left/right: aspect step, gated while streaming"),
    (0x36F4C, "f1a8ffeb", "bl", "v6_aspect_gate", "video left/right: aspect step, gated while streaming"),
    (0x36F7C, "e5a8ffeb", "bl", "v6_aspect_gate", "video left/right: aspect step, gated while streaming"),
    (0x356E0, "5c00000a", "b", "v6_rec_gate", "record start: beq kept, gated while streaming"),
]
# 1080p webcam: frame descriptor 1920x1080, and APMode 0x106's target 1920x1080
# (full sensor, no crop). [0xf894] is a stock literal 0x438; 1080 has no ARM immediate.
# For 720p (1280x720 centre crop): delete these four rows and the four "V6D" rows of
# HOOKMAP in tests/test_v5.py, then run build.sh.
V6_DATA = [
    (0xF3B0A, "0005d002", "80073804", "UVC frame 1: 1280x720 -> 1920x1080"),
    (0xF3B16, "00201c00", "00483f00", "UVC frame 1: dwMaxVideoFrameBufferSize 1920*1080*2"),
    (0x1F2E4, "2d2ea0e3", "a8279fe5", "0x106 target height: mov r2,#0x2d0 -> ldr r2,[pc,#0x7a8] (=0x438)"),
    (0x1F2F0, "053ca0e3", "783ea0e3", "0x106 target width: mov r3,#0x500 -> mov r3,#0x780"),
]

# CANARY=1 (docs/hooks.md): same boot-time hooks and stubs, payload from canary/canary.c.
# The canary leaves the unplug pool word (G4) as in the base image; it only matters
# after a mount.
CANARY = os.environ.get("CANARY") == "1"

# Pool words and data patches, by file offset. The peripheral task stack goes to
# 4 KB (docs/hooks.md); it is used at boot before the updater, proven by the canary.
G4 = (0x36664, "05800000", "02800000", "unplug pool: 0x8005 back to 0x8002, the payload posts the uninit")
STACK4K = (0xEAAFC, "00080000", "00100000", "peripheral task stack 0x800 -> 0x1000 B (InitTask def 0xda8ec)")
DATA = [STACK4K] if CANARY else [G4, STACK4K, *V6_DATA]
if not CANARY:
    HOOKS += V6_HOOKS

# The MSC eject callback: stock reaches it with blx r2 at 0x4e740, so a Thumb
# target needs bit 0 set.
WORD_HOOKS = [(0xE0848, "30e20b00", "v5_eject_cb", True, "LUN+0x40 eject callback: note the handback")]

# G7, the auto-off variant covering VBUS states 1 and 2. Disassembled in the image:
# cmp r0,#0 / movne fp,#0 / strne fp,[r8] / bne 0x22268, so the counter is held while
# the state is 1 or 2.
BYTE_HOOKS = [
    (0x32158, "01", "00", "auto-off: cmp r0,#1 -> cmp r0,#0"),
    (0x3215F, "03", "13", "auto-off: moveq -> movne"),
    (0x32163, "05", "15", "auto-off: strne, covering states 1 and 2"),
    (0x32167, "0A", "1A", "auto-off: bne 0x22268"),
]

for at, old, kind, val, why in HOOKS:
    old = bytes.fromhex(old)
    off = at + B
    assert d[off : off + len(old)] == old, (hex(at), d[off : off + len(old)].hex(), old.hex())
    assert kind in ("b", "bal", "bl"), kind
    t = sym[val]
    assert t % 2 == 0, f"{val} is not an ARM symbol"
    assert GAP <= t < GAP_END, (val, hex(t))
    o = struct.unpack("<I", old)[0]
    new = bl(at, t, o) if kind == "bl" else branch(at, t, o, always=kind == "bal")
    assert len(new) == len(old)
    d[off : off + len(new)] = new
    print(f"RAM {at:#07x} file {off:#08x}: {old.hex()} -> {new.hex()}  {why}")

for at, old, name, thumb, why in WORD_HOOKS:
    old = bytes.fromhex(old)
    off = at + B
    assert d[off : off + len(old)] == old, (hex(at), d[off : off + len(old)].hex(), old.hex())
    t = sym[name]
    if thumb:
        t |= 1
    assert GAP <= (t & ~1) < GAP_END, (name, hex(t))
    new = struct.pack("<I", t)
    d[off : off + len(new)] = new
    print(f"RAM {at:#07x} file {off:#08x}: {old.hex()} -> {new.hex()}  {why} ({name})")

for off, old, new, why in DATA:
    oldb, newb = bytes.fromhex(old), bytes.fromhex(new)
    assert d[off : off + len(oldb)] == oldb, (hex(off), d[off : off + len(oldb)].hex(), old)
    d[off : off + len(newb)] = newb
    print(f"file {off:#08x}: {old} -> {new}  {why}")

for off, old, new, why in BYTE_HOOKS:
    assert d[off] == int(old, 16), (hex(off), d[off : off + 1].hex(), old)
    d[off] = int(new, 16)
    print(f"file {off:#08x}: {old} -> {new}  {why}")

# code into the gap; the gap must still be the stock zero padding
f0 = GAP + B
assert set(d[f0 : GAP_END + B]) == {0}
d[f0 : f0 + len(code)] = code

# load length must cover code, data and bss
old_len = struct.unpack_from("<I", d, LOADLEN)[0]
assert old_len == 0x736, hex(old_len)
new_len = (end + 0x200 + 0x1FF) // 0x200
if CANARY:
    # the canary proves the boot loader copy the release ships with: the whole gap
    new_len = max(new_len, 0x741)
assert new_len <= LOADLEN_MAX, (hex(new_len), hex(LOADLEN_MAX))
struct.pack_into("<I", d, LOADLEN, new_len)
print(f"load length {old_len:#x} -> {new_len:#x} sectors, RAM 0..{new_len * 0x200 - 0x200:#x}")
print(f"payload code+data {len(code)} B, end {end:#x}, gap left {GAP_END - end} B")

# no hook may land inside the payload, and no two hooks may overlap
regions = [(a + B, a + B + 4) for a, *_ in HOOKS]
regions += [(a + B, a + B + 4) for a, _, _, _, _ in WORD_HOOKS]
regions += [(o, o + 4) for o, _, _, _ in DATA]
regions += [(o, o + 1) for o, _, _, _ in BYTE_HOOKS]
regions.sort()
for (a0, a1), (b0, _) in itertools.pairwise(regions):
    assert a1 <= b0, (hex(a0), hex(b0))
for a0, a1 in regions:
    assert a1 <= f0 or a0 >= f0 + len(code), hex(a0)

assert len(d) == SIZE
if TEST_ONLY:
    assert os.path.basename(dst).upper() != FW_NAME, f"a test-only build may not be named {FW_NAME}"
    d += b"\xff" * (TEST_PAD - SIZE)
open(dst, "wb").write(d)
print(dst, len(d), hashlib.sha256(d).hexdigest())
if TEST_ONLY:
    marker = os.path.join(os.path.dirname(dst), "DO-NOT-FLASH")
    open(marker, "w").write(
        f"payload end {end:#x} is above 0xe8000: crt0 zeroes it at boot\nemulator only, the camera would not boot\n"
    )
    print("*** " + marker + ": THIS IMAGE IS NOT FLASHABLE, emulator only ***")
