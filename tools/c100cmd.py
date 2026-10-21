#!/usr/bin/env python3
# Send one command line to a Godox C100 running this firmware as a USB webcam, through the
# webcam's UVC extension unit (docs/webcam.md). Linux only, standard library only; works while
# another program (ustreamer) streams from the camera.
# usage: c100cmd.py [--device /dev/videoN] COMMAND...
#   c100cmd.py display off
#   c100cmd.py drive
import argparse
import ctypes
import errno
import fcntl
import glob
import os
import platform
import sys
import time

# the webcam's USB IDs (device descriptor at stock RAM 0xe3b14, docs/usb.md)
VID, PID = 0x1B3F, 0x2002
# the extension unit (src/xu.h)
XU_ID, XU_SEL = 6, 1
# include/uapi/linux/usb/video.h
UVC_SET_CUR, UVC_GET_CUR, UVC_GET_LEN, UVC_GET_INFO = 0x01, 0x81, 0x85, 0x86
WAIT = 3.0  # s for the camera to take the line; it looks about every 250 ms
POLL = 0.02
SYSFS = "/sys/class/video4linux"


# include/uapi/linux/uvcvideo.h, with this machine's native alignment: 16 B on 64-bit, 12 B on
# 32-bit ARM
class XuQuery(ctypes.Structure):
    _fields_ = [
        ("unit", ctypes.c_uint8),
        ("selector", ctypes.c_uint8),
        ("query", ctypes.c_uint8),
        ("size", ctypes.c_uint16),
        ("data", ctypes.c_void_p),
    ]


# include/uapi/asm-generic/ioctl.h; these architectures have their own encoding
if platform.machine().lower().startswith(("alpha", "mips", "ppc", "powerpc", "sparc", "parisc")):
    sys.exit(f"c100cmd: {platform.machine()} encodes ioctl numbers its own way, not supported")
_IOC_WRITE, _IOC_READ = 1, 2
assert ctypes.sizeof(XuQuery) < 1 << 14
UVCIOC_CTRL_QUERY = (_IOC_READ | _IOC_WRITE) << 30 | ctypes.sizeof(XuQuery) << 16 | ord("u") << 8 | 0x21

# what the camera's request error codes become (drivers/media/usb/uvc/uvc_video.c)
WHY = {
    errno.ENOENT: f"no extension unit {XU_ID} on the camera's video chain: it does not run this firmware",
    errno.EBUSY: "the camera has not taken the last line yet",
    errno.EACCES: "the camera's features are off: no card, or the overlay did not load (LOG.TXT `ovl err N`)",
    errno.EILSEQ: "the camera's features are off: no card, or the overlay did not load (LOG.TXT `ovl err N`)",
    errno.EIO: "the camera refused the request",
    errno.ENODEV: "the camera went away (drive, webcam and reboot restart it)",
    errno.EPROTO: "the camera went away (drive, webcam and reboot restart it)",
    errno.ESHUTDOWN: "the camera went away (drive, webcam and reboot restart it)",
    errno.ETIMEDOUT: "the camera did not answer",
}


def find():
    """the capture node of the one C100 webcam, found through sysfs"""
    found = []
    for node in sorted(glob.glob(os.path.join(SYSFS, "video*"))):
        try:
            with open(os.path.join(node, "index")) as f:
                if f.read().strip() != "0":
                    continue  # uvcvideo's metadata node
            usb = os.path.dirname(os.path.realpath(os.path.join(node, "device")))
            with open(os.path.join(usb, "idVendor")) as f:
                vid = int(f.read(), 16)
            with open(os.path.join(usb, "idProduct")) as f:
                pid = int(f.read(), 16)
        except (OSError, ValueError):
            continue
        if (vid, pid) == (VID, PID):
            found.append("/dev/" + os.path.basename(node))
    if not found:
        sys.exit(f"c100cmd: no {VID:04x}:{PID:04x} webcam found; is the camera plugged in as the webcam?")
    if len(found) > 1:
        sys.exit(f"c100cmd: more than one C100 webcam ({', '.join(found)}), pick one with --device")
    return found[0]


def query(fd, dev, what, n, data=b""):
    buf = (ctypes.c_uint8 * n).from_buffer_copy(data.ljust(n, b"\0"))
    q = XuQuery(XU_ID, XU_SEL, what, n, ctypes.addressof(buf))
    try:
        fcntl.ioctl(fd, UVCIOC_CTRL_QUERY, q)
    except OSError as e:
        why = WHY.get(e.errno, "")
        sys.exit(f"c100cmd: {dev}: query {what:#04x}: {e.strerror}{': ' + why if why else ''}")
    return bytes(buf)


def main():
    ap = argparse.ArgumentParser(description="Send a CMD.TXT line to a C100 webcam over USB.")
    ap.add_argument("--device", help="the camera's /dev/videoN (default: found by USB ID)")
    ap.add_argument("command", nargs="+", help="the line, e.g. `display off` or `drive`")
    a = ap.parse_args()
    text = " ".join(a.command)
    if not text.strip() or not all(" " <= c <= "~" for c in text):
        sys.exit("c100cmd: the line must be printable ASCII on one line, not empty")
    line = text.encode("ascii")
    dev = a.device or find()
    try:
        fd = os.open(dev, os.O_RDWR)
    except OSError as e:
        sys.exit(f"c100cmd: {dev}: {e.strerror}")
    try:
        size = int.from_bytes(query(fd, dev, UVC_GET_LEN, 2), "little")
        info = query(fd, dev, UVC_GET_INFO, 1)[0]
        if info & 3 != 3 or not size:
            sys.exit(f"c100cmd: {dev}: unit {XU_ID} selector {XU_SEL}: GET_INFO {info:#x}, length {size}: not ours")
        if len(line) > size:
            sys.exit(f"c100cmd: the line is {len(line)} B, the camera takes at most {size}")
        query(fd, dev, UVC_SET_CUR, size, line)
        print(f'{dev}: sent "{line.decode()}"')
        end = time.monotonic() + WAIT
        while True:
            held = query(fd, dev, UVC_GET_CUR, size)
            if not any(held):
                print(f"{dev}: taken")
                return 0
            if time.monotonic() > end:
                text = held.rstrip(b"\0").decode("ascii", "replace")
                sys.exit(f'c100cmd: {dev}: still waiting after {WAIT:g} s: "{text}" (see LOG.TXT)')
            time.sleep(POLL)
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
