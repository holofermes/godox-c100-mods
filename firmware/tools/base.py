# Stock V1.1.6 -> base image: USB webcam on plug-in instead of the USB drive, and no
# auto power-off while USB power is present. tools/patch.py builds on this image.
# usage: base.py STOCK.BIN OUT
import hashlib
import struct
import sys

STOCK_SHA = "411a553fc58810a1ea5b04ef46819deea317d574246ef5870c31d79d68f95036"
BASE_SHA = "bfe8a65b49b258bcd2f7ba2886cb97b5c1858e5c3bc1f15038ac2730039056a7"
SIZE = 1841152
B = 0x10200  # file offset = app RAM + B

src, dst = sys.argv[1], sys.argv[2]
with open(src, "rb") as f:
    d = bytearray(f.read())
assert len(d) == SIZE, f"{src}: {len(d)} B, stock V1.1.6 is {SIZE} B"
assert hashlib.sha256(d).hexdigest() == STOCK_SHA, f"{src} is not the stock V1.1.6 GP_CARDVR_UPGRADE.BIN"


def bl(at, to):
    return struct.pack("<I", 0xEB000000 | (((to - (at + 8)) >> 2) & 0xFFFFFF))


PATCHES = [
    # USB task message words posted on plug-in and on PC disconnect (0x8001 drive init,
    # 0x8002 drive uninit, 0x8003 webcam init, 0x8005 webcam uninit)
    (0x2F9CC, "01800000", "03800000", "pool 0x1f7cc: plug-in 0x8001 drive init -> 0x8003 webcam init"),
    (0x3680C, "02800000", "05800000", "pool 0x2660c: PC disconnect 0x8002 drive uninit -> 0x8005 webcam uninit"),
    # USB task message 0x8007 (stream start), RAM 0x3f0d4..0x3f0e7: run the encoder in
    # APMode 0x106, whose JPEG output feeds the UVC endpoint
    (
        0x3F0D4 + B,
        "8242ffeb000050e3d8ffff0ae0009fe59b5100eb",
        (
            bl(0x3F0D4, 0xFACC)  # stop recording
            + struct.pack("<I", 0xE59F00E8)  # ldr r0, =0x106 (pool word below)
            + bl(0x3F0DC, 0xF98C)  # video_encode_sensor_on(0x106)
            + struct.pack("<I", 0xE3A00000)  # mov r0, #0
            + bl(0x3F0E4, 0xFAE4)  # preview_on(0)
        ).hex(),
        "0x8007 handler: stop record, video_encode_sensor_on(0x106), preview_on(0)",
    ),
    (0x3F1C8 + B, "e8530d00", "06010000", "pool 0x3f1c8: string pointer (its only reader was 0x3f0e0) -> 0x106"),
    # 0x6004 handler, every 5 s: zeroes the auto-off counter in VBUS state 2 (host).
    # With the webcam a PC never leaves state 1, so hold it in state 1.
    (0x22080 + B, "020050e3", "010050e3", "auto-off: cmp r0,#2 -> cmp r0,#1, counter held while VBUS is present"),
]

for off, old, new, why in PATCHES:
    old, new = bytes.fromhex(old), bytes.fromhex(new)
    assert len(old) == len(new)
    assert d[off : off + len(old)] == old, (hex(off), d[off : off + len(old)].hex())
    d[off : off + len(new)] = new
    print(f"file {off:#08x}: {old.hex()} -> {new.hex()}  {why}")

assert hashlib.sha256(d).hexdigest() == BASE_SHA, "base image hash mismatch"
with open(dst, "wb") as f:
    f.write(d)
print(dst, len(d), BASE_SHA)
