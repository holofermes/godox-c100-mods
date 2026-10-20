# Patch tests under emulation. usage, from the repo root after ./build.sh:
#   uv run --group test python tests/test_v5.py build/GP_CARDVR_UPGRADE.BIN obj/v5.sym [BASE]
# BASE defaults to obj/base.bin, the image tools/patch.py patched. ONLY=name runs only the
# tests whose function name contains it.
# Not with python -I: it drops the script directory and `import emu` fails.
import os
import struct
import sys
import zlib

from emu import (
    A_EJECT,
    A_GPIO_SET,
    A_LUN_REG,
    A_PRINT_BUF,
    A_PRINTF,
    A_START_STOP,
    APQ_OBJ,
    B_CAPTURE_BUSY,
    B_CARD_OK,
    B_F_HELD,
    B_MODE,
    B_MOUNT_FAIL,
    B_REC_FLAGS,
    B_USB_POLL_EN,
    B_USB_STATE,
    CALLEE_SAVED,
    GAP,
    GAP_END,
    GPIO,
    H_LUN0,
    LUN0,
    MSC_CTX,
    OVL_BASE,
    OVL_FLASH,
    OVL_HDR,
    OVL_MAGIC,
    OVL_TOP,
    RET,
    SD_OPS,
    STACK,
    USBQ_H,
    W_APP_STATE,
    W_NV_DIR,
    W_NV_MUTEX,
    B,
    Card,
    EmuError,
    Harness,
    Machine,
)
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UcError
from unicorn.arm_const import (
    UC_ARM_REG_CPSR,
    UC_ARM_REG_R0,
    UC_ARM_REG_R1,
    UC_ARM_REG_R2,
    UC_ARM_REG_R3,
    UC_ARM_REG_R4,
    UC_ARM_REG_R5,
    UC_ARM_REG_R6,
    UC_ARM_REG_R7,
    UC_ARM_REG_R8,
    UC_ARM_REG_R9,
    UC_ARM_REG_R10,
    UC_ARM_REG_R12,
    UC_ARM_REG_SP,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_DEFAULT = "display=on\nled=on\nsound=on\nlog=on\nusb_photo=drive\nusb_video=webcam\n"

# struct offsets, v5.h
CFG_DISPLAY, CFG_LED, CFG_SOUND, CFG_LOG, CFG_USB = 0, 1, 2, 3, 4
ST_ACTIVE, ST_PENDING, ST_RELEASED, ST_STALE, ST_TOGGLE, ST_SOUND, ST_BOOTED, ST_EJECTING = range(8)
ST_LOADED, ST_LOAD_ERR = 16, 17  # after next_cmd_ms and next_log_ms
LED_OFF, LED_WANT43, LED_WANT95 = 0, 1, 2

# Peripheral task: 4096 B stack (InitTask def at 0xdaa1c, stack 0x800 in stock, 0x1000 with
# the patch, docs/hooks.md). The task function 0x21e60 pushes 9 registers (0x21e64) and
# drops sp by 0x2c (0x21e80): 80 B under its entry sp when the 0x6001 case reaches 0x22360.
# An IRQ taken in the task saves 18 words on the task stack (lr, r0-r14, spsr, critical
# nesting: 0x5c89c..0x5c8d8).
TASK_STACK, TASK_FRAME, IRQ_FRAME = 4096, 80, 72

# The hook map, as tools/patch.py applies it: (id, RAM, original, kind, target)
HOOKMAP = [
    ("LOG", 0x537B4, "0000d4e5", "b", "logcap_stub"),
    ("SET", 0x37FA0, "58d08de2", "b", "settings_stub"),
    ("D1", 0x22360, "ecfbffeb", "bl", "tick_stub"),
    ("G1", 0x1F5B0, "5f00a0e3", "b", "plug_stub"),
    ("G2", 0x1F530, "737a02eb", "bl", "unplug_stub"),
    ("G3a", 0x22260, "8500000a", "bal", "usb_poll_gate_stub"),
    ("G3b", 0x2247C, "d4f3ffeb", "bl", "usb_tick_stub"),
    ("G4", 0x3680C - B, "05800000", "bytes", "02800000"),
    ("G6", 0xE0978, "d4e20b00", "word", "eject_stub"),
    ("G7", 0x32280 - B, "01", "bytes", "00"),
    ("G7", 0x32287 - B, "03", "bytes", "13"),
    ("G7", 0x3228B - B, "05", "bytes", "15"),
    ("G7", 0x3228F - B, "0a", "bytes", "1a"),
    ("K1", 0x1DDA4, "0300008a", "b", "long_select_stub"),
    ("L1", 0x433F4, "0036a0e1", "b", "v5_gpio_set"),
    ("L2", 0x1F5B4, "7f8f00eb", "bl", "v5_get95"),
    ("L3", 0x1F65C, "558f00eb", "bl", "v5_get95"),
    ("S1", 0x22A80, "f0402de9", "b", "v5_sound_gate"),
    ("STACK", 0xEAC2C - B, "00080000", "bytes", "00100000"),
    ("HEAP", 0x5F46C, "ffdffb00", "bytes", "ffdffa00"),
    ("V6A", 0x2EC48, "e4c9ffeb", "bl", "v6_aspect_gate"),
    ("V6A", 0x37104, "b5a8ffeb", "bl", "v6_aspect_gate"),
    ("V6A", 0x37134, "a9a8ffeb", "bl", "v6_aspect_gate"),
    ("V6R", 0x35898, "5c00000a", "b", "v6_rec_gate"),
    ("V6D", 0x1F2E4 - B, "2d2ea0e3", "bytes", "a8279fe5"),
    ("V6D", 0x1F2F0 - B, "053ca0e3", "bytes", "783ea0e3"),
    ("V6S", 0xF510, "be00d4e1", "bl", "v6_size"),
    ("V6S", 0x1F6E0 - B, "b610d4e1", "bytes", "ba11d4e1"),
    ("V6S", 0x1F6E4 - B, "b820d4e1", "bytes", "bc21d4e1"),
    ("V6S", 0x1F714 - B, "b031d4e1", "bytes", "0000a0e1"),
    ("V6P", 0x629D4 - B, "010053e3", "bytes", "060053e3"),
    ("V6P", 0x629D8 - B, "a6ffff1a", "bytes", "a6ffff8a"),
    ("V6C", 0x3EF00, "d0390e00", "word", "v6_cfg"),
    ("V6C", 0x52488, "d0390e00", "word", "v6_cfg+0xbe"),
]
LOADLEN, LOADLEN_SHIP = 0x1002E, 0x741
SIZE, TEST_PAD = 1841152, 0x200000  # tools/patch.py: a test-only build is padded with 0xFF
B_UPD_CHECKED = 0x123D34  # 0 at boot, 2 = updater started, 1 = after the first mount call that did not start one
W_MSDC_CB = 0x124294  # SET_CONFIG callback usb_msdc_init leaves behind (fw.h)
# every C entry a hook can reach; none may run before load_settings arms the patch
V5_ENTRIES = (
    "logcap",
    "v5_tick",
    "act_long_select",
    "act_sound_gate",
    "v5_usb_plugin",
    "v5_usb_unplug",
    "v5_usb_tick",
    "v5_eject_cb",
)


def boot(cfg_text=CFG_DEFAULT, cmd=None, files=None, mode=0):
    f = dict(files or {})
    if cfg_text is not None:
        f["C100.TXT"] = cfg_text
    if cmd is not None:
        f["CMD.TXT"] = cmd
    m = Machine(IMAGE, SYM, Card(f))
    mount(m)
    # what the photo/video mount-OK handler does next (0x2eddc, 0x36498)
    m.poke8(B_USB_POLL_EN, 1)
    m.poke16(W_APP_STATE, 0x102)
    m.poke8(B_MODE, mode)
    m.boot_trace, m.boot_violations = m.trace, m.violations
    m.reset()
    return m


def mount(m, r4=0xC0DE0004):
    """The stock mount path reaching the SET hook in the storage task. The VERSION.TXT
    writer 0x37f34 holds 0x58 B of locals over its pushed r4 and lr there."""
    m.task("storage")
    sp = STACK - 0x100
    m.poke32(sp + 0x58, r4)
    m.poke32(sp + 0x5C, RET)
    m.call_arm(0x37FA0, sp=sp)
    if m.u8(B_UPD_CHECKED) != 2:  # 0x3e574..0x3e580 after every mount call: 1 unless 2
        m.poke8(B_UPD_CHECKED, 1)
    m.task("periph")
    return sp


def tick(m, ms=0):
    """One 0x6001 pass of the peripheral task, entered at the D1 site."""
    m.ticks += ms
    m.task("periph")
    m.call_arm(0x22360, sp=STACK - 0x100, until=0x22364)


def stv(m, off):
    return m.u8(m.sym["st"] + off)


def cfgv(m, off):
    return m.u8(m.sym["cfg"] + off)


def log(m):
    return m.card.get("LOG.TXT").decode("latin1")


def allow_reboot(m):
    m.poke8(B_CAPTURE_BUSY, 0)
    m.poke8(B_REC_FLAGS, 0)
    m.poke16(W_APP_STATE, 0x102)


def leaks(m):
    """heap blocks still live, minus the log ring load_settings keeps for good"""
    return {hex(a): n for a, n in m.live.items() if n != 8192}


# ---------------------------------------------------------------- image
def t_image(h):
    h.section = "image: hook map"
    print(f"\nimage: the {len(HOOKMAP)} hook-map rows, and nothing else changed")
    d = open(IMAGE, "rb").read()
    base = open(BASE, "rb").read()
    S = {}
    for line in open(SYM):
        a, _, name = line.split()
        S[name] = int(a, 16)
    touched = set()
    for hid, at, orig, kind, val in HOOKMAP:
        off = at + B
        o = bytes.fromhex(orig)
        touched.update(range(off, off + len(o)))
        h.eq(base[off : off + len(o)].hex(), orig, f"{hid} {at:#x}: base holds the original bytes")
        got = d[off : off + len(o)]
        if kind == "bytes":
            h.eq(got.hex(), val, f"{hid} {at:#x}: {orig} -> {val}")
        elif kind == "word":
            name, _, add = val.partition("+")
            want = S[name] + int(add or "0", 0)
            h.ok(want % 2 == 0, f"{hid} {at:#x}: {val} is ARM code or word data")
            h.eq(struct.unpack("<I", got)[0], want, f"{hid} {at:#x}: word = {val}")
        else:
            w, ow = struct.unpack("<I", got)[0], struct.unpack("<I", o)[0]
            is_br = (w >> 25) & 7 == 5
            link = (w >> 24) & 1
            tgt = at + 8 + (((w & 0xFFFFFF) ^ 0x800000) - 0x800000) * 4
            cond_ok = (w >> 28) == (0xE if kind == "bal" else ow >> 28)
            good = is_br and link == (kind == "bl") and tgt == S[val] and cond_ok
            how = "" if (ow >> 28) == 0xE else " (always)" if kind == "bal" else " (cond kept)"
            h.ok(
                good,
                f"{hid} {at:#x}: {kind}{how} -> {val}",
                f"(word {w:08x}, {'target ' + hex(tgt) if is_br else 'not a branch'}; {val} = {S[val]:#x})",
            )
    touched.update(range(LOADLEN, LOADLEN + 4))
    end = S["__gap_end"]
    touched.update(range(GAP + B, end + B))
    olen = S["__ovl_load_end"] - S["__ovl_start"]
    touched.update(range(OVL_FLASH, OVL_FLASH + OVL_HDR + olen))
    stray = [o for o in range(SIZE) if d[o] != base[o] and o not in touched]
    h.ok(
        not stray,
        "no byte differs from the base outside the hook map, load length, gap and overlay",
        f"({len(stray)} B, first at file {stray[0]:#x})" if stray else "",
    )
    h.section = "image: overlay"
    magic, hlen, crc, pad = struct.unpack_from("<4I", d, OVL_FLASH)
    body = d[OVL_FLASH + OVL_HDR : OVL_FLASH + OVL_HDR + hlen]
    h.eq((magic, pad), (OVL_MAGIC, 0), f"overlay header at file {OVL_FLASH:#x}: magic COVL, pad 0")
    h.eq(hlen, olen, "header length = the body the gap was linked against (__ovl_load_end - __ovl_start)")
    h.eq(crc, zlib.crc32(body), "header CRC-32 = zlib.crc32 of the body")
    xa = S["ovl_expect"]
    h.eq(
        struct.unpack_from("<2I", d, xa + B),
        (olen, crc),
        f"gap's ovl_expect at {xa:#x} = this build's overlay length and CRC (the loader refuses any other)",
    )
    h.eq((S["__ovl_base"], S["__ovl_start"]), (OVL_BASE, OVL_BASE + OVL_HDR), "header at 0xff0000, body at 0xff0010")
    rd = (OVL_HDR + olen + 0xFF) & ~0xFF
    h.ok(
        S["__ovl_end"] <= OVL_TOP and OVL_BASE + rd <= OVL_TOP,
        f"overlay with .bss ({S['__ovl_end']:#x}) and the page-rounded read ({OVL_BASE + rd:#x}) fit below 0x1000000",
    )
    h.ok(
        (LOADLEN_SHIP - 1) * 0x200 + B <= OVL_FLASH and OVL_FLASH + rd <= 0x170000,
        "overlay in the file padding: past the boot copy (0xf8200), before the resources (0x170000)",
    )
    h.ok(set(base[OVL_FLASH : OVL_FLASH + rd]) == {0}, "the base image is zero where the overlay goes")
    h.ok(
        not any("veneer" in n or n.endswith(("_from_arm", "_from_thumb")) for n in S),
        "no linker veneers (gap and overlay call each other through registers)",
    )
    name = os.path.basename(IMAGE).upper()
    if end > GAP_END:
        h.ok(name != "GP_CARDVR_UPGRADE.BIN", "test-only build is not named GP_CARDVR_UPGRADE.BIN")
        h.ok(
            len(d) == TEST_PAD and set(d[SIZE:]) == {0xFF},
            "test-only build padded with 0xFF to 2 MiB, past the updater's 1,846,000 B gate",
            f"({len(d)} B)",
        )
        # a build with GAP_END raised links the end of the gap into stock .bss, which crt0 zeroes
        print(
            f"  WARN gap ends at {end:#x}, past stock .bss start {GAP_END:#x}: what lives there is "
            f"zeroed at boot on the camera (the emulator does not run crt0)"
        )
    else:
        h.eq(len(d), SIZE, "flashable build is the stock size")
        h.eq(struct.unpack_from("<I", d, LOADLEN)[0], LOADLEN_SHIP, "load length 0x741: the whole gap")
        h.ok(end <= GAP_END, f"gap ends at {end:#x}, inside 0xe8000")
        h.ok(set(d[end + B : GAP_END + B]) <= {0}, "the rest of the copied gap is the stock zero padding")


def t_dispatch(h):
    h.section = "seam D1 dispatch"
    print("\nseam D1 through the stock dispatch: one 0x6001 message, back to the receive")
    # after the queue receive (bl 0x51c at 0x21ebc, so lr = 0x21ec0) the task loop
    # dispatches [sp+0x24] through the table at 0x21ed4; 0x6001 is entry 1 = 0x22360
    m = boot()
    sp = STACK - 0x400
    m.poke32(sp + 0x24, 0x6001)
    loop = {
        UC_ARM_REG_R4: 0x131FC4,
        UC_ARM_REG_R6: 0x121720,
        UC_ARM_REG_R7: 0x132A14,
        UC_ARM_REG_R8: 0x12171C,
        UC_ARM_REG_R9: 0x121710,
        UC_ARM_REG_R10: 0x131FE0,
    }
    m.reset()
    try:
        m.call_arm(0x21EC8, 0, sp=sp, lr=0x21EC0, regs=loop, until=0x51C, limit=2_000_000)
        back = True
    except EmuError as e:
        back = str(e)
    h.ok(back is True, "the task loop gets back to its queue receive 0x51c", f"({back})")
    h.eq(m.hits["v5_tick"], 1, "v5_tick ran once for one message")
    h.eq(m.reg(UC_ARM_REG_SP) if back is True else None, sp, "task sp intact at the receive")


# ---------------------------------------------------------------- disarmed
def t_disarmed(h):
    h.section = "disarmed hooks"
    print("\nbefore load_settings arms the patch, every hook runs only its displaced stock instruction")
    print("  (the overlay is not loaded yet: its region holds UDF words)")
    m = Machine(IMAGE, SYM, Card({"C100.TXT": "led=off\nsound=off\n"}))
    m.gpio_input(95, 0)
    sp = STACK - 0x200

    m.task("ap")
    m.call_arm(A_PRINTF, m.strp("disarmed-line\r\n"), sp=sp)
    h.eq("".join(chr(t[1]) for t in m.calls("uart")), "disarmed-line\r\n", "LOG: printf goes out the stock UART path")
    h.eq(m.reg(UC_ARM_REG_SP), sp, "LOG: printf returns with sp intact")

    m.task("periph")
    m.reset()
    m.call_arm(0x22360, sp=sp, until=0x22364)
    h.eq(m.names(), ["seg_refresh"], "D1: seg_refresh only")
    h.eq(m.reg(UC_ARM_REG_SP), sp, "D1: sp intact")

    for en, want in ((0, []), (1, ["vbus"])):
        m.poke8(B_USB_POLL_EN, en)
        m.reset()
        m.call_arm(0x22254, sp=sp, until=0x22264)
        h.eq(m.names(), want, f"G3a/G3b: poll enable {en}, {'VBUS poll only' if en else 'no poll'}")

    m.reset()
    plug(m)
    h.eq([t[2] for t in m.calls("qsend")], [0x8003], "G1/L2: stock insert path posts the base image's webcam init")
    m.reset()
    m.call_arm(0x1F530, sp=sp, until=0x1F534)
    h.eq(m.names(), ["usb_off"], "G2: stock 0xbdf04 only")
    h.eq(m.reg(UC_ARM_REG_SP), sp, "G2: sp intact")

    m.reset()
    m.call_arm(0x1DD9C, sp=sp, regs={UC_ARM_REG_R5: 0x1C, UC_ARM_REG_R4: 0, UC_ARM_REG_R3: 0x12170A}, until=0x1DDB8)
    h.eq((m.reg(UC_ARM_REG_SP), stv(m, ST_TOGGLE)), (sp, 0), "K1: straight to 0x1ddb8, no toggle")

    led = m.sym["led"]  # overlay data, not loaded yet
    m.poke8(led + LED_OFF, 1)  # as if led=off were already applied
    want = (m.u8(led + LED_WANT43), m.u8(led + LED_WANT95))
    m.call_arm(A_GPIO_SET, 43, 1)
    m.call_arm(A_GPIO_SET, 95, 1)
    h.eq((m.gpio(43), m.gpio(95)), (1, 1), "L1: gpio_set passes 43 and 95 through")
    h.eq((m.u8(led + LED_WANT43), m.u8(led + LED_WANT95)), want, "L1: nothing recorded")
    m.gpio_input(95, 1)
    h.eq(m.call_arm(0x1F65C, 0x5F, sp=sp, until=0x1F660), 1, "L3: GPIO 95 read is the real pin")
    m.gpio_input(95, 0)

    m.task("ap")
    m.reset()
    h.eq(m.call_arm(0x22A80, 3, sp=sp), 0xFFFFFFFF, "S1: chime 3 returns -1")
    h.eq(stv(m, ST_SOUND), 3, "S1: and is parked in sound_pending")
    h.not_called("play", "S1: body not entered for the chime", m=m)
    h.eq(m.call_arm(0x22A80, 1, sp=sp), 0, "S1: id 1 plays as stock")
    h.eq(m.reg(UC_ARM_REG_SP), sp, "S1: sp intact")

    h.eq({n: m.hits[n] for n in V5_ENTRIES if m.hits[n]}, {}, "no payload C entered from any hook")

    m.task("usb")
    m.reset()
    h.eq(m.call("eject_stub", 0), 0, "G6: eject_stub returns the stock callback's 0")
    h.called("eject", "G6: stock 0xbe2d4 runs", m=m)
    h.not_called("v5_eject_cb", "G6: the overlay's v5_eject_cb not entered", m=m)
    h.eq(stv(m, ST_RELEASED), 0, "G6: host_released not set")
    h.eq(stv(m, ST_EJECTING), 0, "G6: ejecting not set")

    print("  arming needs st.booted and [0x123d34] == 1")
    m.task("periph")
    for booted, upd, why in (
        (1, 0, "SET ran, 0x3e580 not yet"),
        (1, 2, "updater started"),
        (0, 1, "mount started no update but SET never ran"),
    ):
        m.poke8(m.sym["st"] + ST_BOOTED, booted)
        m.poke8(B_UPD_CHECKED, upd)
        m.reset()
        m.call_arm(0x22360, sp=sp, until=0x22364)
        h.eq(m.names(), ["seg_refresh"], f"booted {booted}, [0x123d34] {upd} ({why}): disarmed")
    m.poke8(m.sym["st"] + ST_BOOTED, 0)
    m.poke8(B_UPD_CHECKED, 0)
    mount(m)
    h.eq(stv(m, ST_BOOTED), 1, "load_settings sets st.booted")
    m.reset()
    tick(m)
    h.called("v5_tick", "armed: D1 reaches v5_tick", m=m)
    m.poke8(B_USB_POLL_EN, 0)
    m.reset()
    m.call_arm(0x22254, sp=sp, until=0x22264)
    n = m.names()
    h.ok("vbus" in n and "v5_usb_tick" in n, "armed: G3a polls without the stock enable", f"({n})")
    h.clean(m)


# ---------------------------------------------------------------- heap reservation
def t_heap(h):
    h.section = "heap reservation"
    print("\nheap setup: main 0x5f35c..0x5f37c with the image's literal, through the stock heap_5 0x5ccb4")
    sp = STACK - 0x100
    regs = {UC_ARM_REG_R4: 0x208AC0, UC_ARM_REG_R5: 0x24AAC0, UC_ARM_REG_R6: 0}  # pool start, heap start (main)
    got = {}
    for lit in (None, 0xFBDFFF):
        m = Machine(IMAGE, SYM)
        if lit is not None:
            m.poke32(0x5F46C, lit)  # stock's literal, for comparison
        top = m.watch_writes(OVL_BASE, OVL_TOP)
        m.call_arm(0x5F35C, sp=sp, until=0x5F37C, regs=regs)
        # 0x5cd54 str r2,[r8]: pxEnd at [0x126a94]; 0x5cd68 str r4,[r0]: free bytes at [0x126aa0]
        got[lit] = (m.u32(0x5F46C), m.u32(0x126A94), m.u32(0x126AA0), len(top))
    lit, end, free, wr = got[None]
    h.eq(lit, 0xFADFFF, "heap-size literal at 0x5f46c: 0xfbdfff lowered by 64 KB")
    h.eq(end, 0xFEFF80, "heap end marker at 0xfeff80")
    h.eq(wr, 0, "the heap setup writes nothing at 0xff0000..0x1000000")
    h.eq(free, 0xFEFF80 - 0x24AAC0, f"heap {free:,} B")
    slit, send, sfree, swr = got[0xFBDFFF]
    h.eq((send, sfree), (0xFFFF80, 14_374_080), "stock literal: end marker 0xffff80, heap 14,374,080 B")
    h.eq(sfree - free, OVL_TOP - OVL_BASE, "the reservation costs the heap exactly 64 KB")


# ---------------------------------------------------------------- overlay loader
def ovl_layout(m):
    olen = m.sym["__ovl_load_end"] - m.sym["__ovl_start"]
    pages = (OVL_HDR + olen + 0xFF) // 0x100
    return olen, pages


def t_loader(h, armed=True):
    h.section = "overlay loader"
    print("\noverlay loader: NOR read through nv_read_page, header, CRC, .bss, caches, then load_settings")
    m = Machine(IMAGE, SYM, Card({"C100.TXT": "log=on\n"}))
    olen, pages = ovl_layout(m)
    snap = {}

    def at_entry(uc, a, size, _):
        if "bss" not in snap:
            b0, b1 = m.sym["__ovl_bss_start"], m.sym["__ovl_end"]
            snap["bss"] = bytes(uc.mem_read(b0, b1 - b0))
            snap["img"] = bytes(uc.mem_read(OVL_BASE, OVL_HDR + olen))
            snap["low"] = m.stack_low

    m.uc.hook_add(UC_HOOK_CODE, at_entry, begin=m.sym["load_settings"] & ~1, end=m.sym["load_settings"] & ~1)
    m.stack_low = STACK
    top = mount(m)
    reads = m.calls("nv_read")
    want = [("nv_read", OVL_FLASH + k * 0x100, OVL_BASE + k * 0x100) for k in range(pages)]
    h.eq([t[:3] for t in reads], want, f"{pages} page reads, NOR {OVL_FLASH:#x}.. into 0xff0000.., header page first")
    h.eq(snap.get("img"), bytes(m.raw[OVL_FLASH : OVL_FLASH + OVL_HDR + olen]), "RAM holds the image's header and body")
    h.eq(set(snap.get("bss", b"x")), {0}, ".bss zeroed before load_settings runs")
    n = m.names()
    order = [x for x in n if x in ("nv_read", "dclean", "icache_inval", "load_settings")]
    h.eq(
        order, ["nv_read"] * pages + ["dclean", "icache_inval", "load_settings"], "reads, D-cache clean, I-cache, entry"
    )
    dc = m.calls("dclean")
    h.eq(dc[0][1:3] if dc else None, (OVL_BASE, m.sym["__ovl_end"] - OVL_BASE), "D-cache clean over header..bss end")
    h.eq(m.dclean_lines, (m.sym["__ovl_end"] - OVL_BASE + 31) // 32, "one cp15 clean per 32 B line")
    h.eq((stv(m, ST_LOADED), stv(m, ST_LOAD_ERR)), (1, 0), "st.loaded 1, load_err 0")
    h.eq(stv(m, ST_BOOTED), 1 if armed else 0, "st.booted " + ("1: load_settings armed" if armed else "0: gate shut"))
    above = snap.get("low", 0) - top
    h.ok(
        above >= 0,
        f"loader frames stay inside the stack the writer held at 0x37fa0 ({above} B above its sp; "
        "nv_read_page's own frames are modelled, not counted)",
    )
    h.clean(m)
    m.reset()
    mount(m)
    h.eq((len(m.calls("nv_read")), m.hits["load_settings"] > 0), (0, True), "next mount: no reload, load_settings")
    m.reset()
    m.poke8(B_UPD_CHECKED, 0)  # a start that kept the gap but ran crt0
    mount(m)
    h.eq(len(m.calls("nv_read")), pages, "first mount of a boot ([0x123d34] == 0) always reloads")

    print("  every failure leaves the gate shut and enters no overlay code")
    body = OVL_FLASH + OVL_HDR

    def other_build(m):
        """A self-consistent overlay of the same length from another build: one body byte
        differs and the header carries that body's correct CRC-32."""
        m.nor[body + olen // 3] ^= 0x5A
        crc = zlib.crc32(bytes(m.nor[body : body + olen]))
        assert crc != struct.unpack_from("<I", m.nor, OVL_FLASH + 8)[0]
        struct.pack_into("<I", m.nor, OVL_FLASH + 8, crc)

    cases = (
        ("nv init not done (no NV mutex)", 1, lambda m: m.poke32(W_NV_MUTEX, 0)),
        ("nv init not done (no resource directory)", 1, lambda m: m.poke32(W_NV_DIR, 0)),
        ("header page read fails", 2, lambda m: m.nv_fail.add(OVL_FLASH)),
        ("last page read fails", 2, lambda m: m.nv_fail.add(OVL_FLASH + (pages - 1) * 0x100)),
        ("bad magic", 3, lambda m: m.nor.__setitem__(OVL_FLASH, m.nor[OVL_FLASH] ^ 0x20)),
        ("length one short", 4, lambda m: m.nor.__setitem__(OVL_FLASH + 4, m.nor[OVL_FLASH + 4] ^ 1)),
        ("length 0xffffffff", 4, lambda m: m.nor.__setitem__(slice(OVL_FLASH + 4, OVL_FLASH + 8), b"\xff" * 4)),
        ("body byte flipped", 5, lambda m: m.nor.__setitem__(body + olen // 2, m.nor[body + olen // 2] ^ 1)),
        ("header CRC flipped", 6, lambda m: m.nor.__setitem__(OVL_FLASH + 8, m.nor[OVL_FLASH + 8] ^ 0x80)),
        ("another build's overlay: same length, consistent header and CRC", 6, other_build),
        ("last body byte flipped", 5, lambda m: m.nor.__setitem__(body + olen - 1, m.nor[body + olen - 1] ^ 1)),
    )
    sp = STACK - 0x200
    for what, err, spoil in cases:
        m = Machine(IMAGE, SYM, Card({"C100.TXT": "led=off\nsound=off\nlog=on\n"}))
        spoil(m)
        m.trap_overlay()
        mount(m)
        m.poke8(B_USB_POLL_EN, 1)
        m.poke16(W_APP_STATE, 0x102)
        tick(m, 3000)
        m.task("ap")
        m.call_arm(A_PRINTF, m.strp("after-failed-load\r\n"), sp=sp)
        chime = m.call_arm(0x22A80, 2, sp=sp)
        m.task("usb")
        m.call("eject_stub", 0)
        m.gpio_input(95, 0)
        plug(m)
        ok = (
            stv(m, ST_LOAD_ERR) == err
            and (stv(m, ST_LOADED), stv(m, ST_BOOTED)) == (0, 0)
            and not m.ovl_blocks
            and not m.hits["load_settings"]
            and not {k: v for k, v in m.hits.items() if k in V5_ENTRIES and v}
            and not m.calls("dclean")
            and not m.calls("icache_inval")
            and [t[2] for t in m.calls("qsend")] == [0x8003]
            and chime == 0xFFFFFFFF
            and m.card.get("LOG.TXT") == f"ovl err {err}\r\n".encode()
        )
        h.ok(
            ok,
            f"{what}: load_err {err}, gate shut, no overlay code, hooks run stock, LOG.TXT `ovl err {err}`",
            f"(err {stv(m, ST_LOAD_ERR)}, loaded {stv(m, ST_LOADED)}, booted {stv(m, ST_BOOTED)}, "
            f"overlay blocks {len(m.ovl_blocks)}, entries {[k for k in V5_ENTRIES if m.hits[k]]}, "
            f"cache {len(m.calls('dclean'))}/{len(m.calls('icache_inval'))}, LOG.TXT {m.card.get('LOG.TXT')!r})",
        )
        h.clean(m, f"{what}: no model violations")
    m = Machine(IMAGE, SYM, Card({"LOG.TXT": "old\r\n"}))
    m.nv_fail.add(OVL_FLASH)
    mount(m)
    h.eq(m.card.get("LOG.TXT"), b"old\r\novl err 2\r\n", "the failure line is appended to an existing LOG.TXT")
    h.eq(m.fds, {}, "and the file is closed")
    m.nv_fail.clear()
    m.reset()
    mount(m)
    h.eq((stv(m, ST_LOAD_ERR), stv(m, ST_LOADED)), (0, 1), "a failed read is retried on the next mount, which loads")

    print("  a card re-inserted while the updater runs ([0x123d34] == 2) reaches SET: v5_set returns at once")
    m = Machine(IMAGE, SYM, Card({"C100.TXT": "log=on\n"}))
    m.trap_overlay()
    m.poke8(B_UPD_CHECKED, 2)
    mount(m)
    h.ok(
        not m.calls("nv_read")
        and not m.hits["load_settings"]
        and not m.ovl_blocks
        and (stv(m, ST_LOADED), stv(m, ST_BOOTED), stv(m, ST_LOAD_ERR)) == (0, 0, 0)
        and m.u8(B_UPD_CHECKED) == 2
        and m.card.names() == ["C100.TXT"],
        "no NOR read, no overlay code, no LOG.TXT line, state untouched",
        f"({m.names()}, card {m.card.names()})",
    )
    m.task("periph")
    m.reset()
    m.call_arm(0x22360, sp=sp, until=0x22364)
    h.eq(m.names(), ["seg_refresh"], "and the hooks stay disarmed")
    h.clean(m)


# ---------------------------------------------------------------- settings
def t_settings(h):
    h.section = "settings load"
    print("\nsettings load")
    m = boot(CFG_DEFAULT)
    h.ok("load_settings" in [t[0] for t in m.boot_trace], "SET hook reached load_settings")
    h.eq(cfgv(m, CFG_DISPLAY), 1, "display=on parsed")
    h.eq(cfgv(m, CFG_LED), 1, "led=on parsed")
    h.eq(cfgv(m, CFG_SOUND), 1, "sound=on parsed")
    h.eq(cfgv(m, CFG_USB), 1, "usb_photo=drive")
    h.eq(cfgv(m, CFG_USB + 1), 2, "usb_video=webcam")
    h.eq(stv(m, ST_BOOTED), 1, "booted set")
    tick(m)
    h.has("C100.TXT ok", "banner names the settings file", m=m)
    h.has("cfg 1111 usb 122", m=m)
    h.eq(leaks(m), {}, "no heap left behind")
    h.ok(not m.boot_violations and not m.violations, "no model violations", f"({m.boot_violations + m.violations})")
    m = boot("# comment\r\n  DISPLAY = Off \r\nusb_photo=webcam\r\nled=NO\r\n")
    h.eq(cfgv(m, CFG_DISPLAY), 0, "CRLF, spaces and case: display off")
    h.eq(cfgv(m, CFG_LED), 0, "led=NO")
    h.eq(cfgv(m, CFG_USB), 2, "usb_photo=webcam")

    print("  a re-insert parses C100.TXT aside, then copies it into cfg in one step with IRQs masked")
    m = boot("display=on\nled=on\n")
    tick(m)
    m.card.f["C100.TXT"] = bytearray(b"display=off\nled=off\nsound=off\n")
    c0 = m.sym["cfg"]
    writes = []

    def wr(uc, access, addr, size, value, _):
        writes.append((len(m.trace), addr, size, uc.reg_read(UC_ARM_REG_CPSR) & 0x80))

    m.uc.hook_add(UC_HOOK_MEM_WRITE, wr, begin=c0, end=c0 + 8)
    m.reset()
    mount(m)
    n = m.names()
    freed = max((i for i, x in enumerate(n) if x == "free"), default=None)  # the C100.TXT buffer
    span = set()
    for _, a, size, _ in writes:
        span.update(range(a, a + size))
    h.ok(
        writes and freed is not None and all(i > freed for i, *_ in writes),
        "cfg written only after the file is parsed and freed",
        f"({len(writes)} writes, trace positions {[w[0] for w in writes]}, free at {freed})",
    )
    h.ok(all(w[3] for w in writes), "every write to cfg with IRQs masked", f"({writes})")
    h.eq(sorted(span), list(range(c0, c0 + 9)), "the copy covers all 9 bytes of cfg")
    h.eq([cfgv(m, i) for i in range(3)], [0, 0, 0], "the new values are in")


def t_settings_off(h):
    h.section = "display and led off"
    print("\ndisplay and led off from C100.TXT")
    m = Machine(IMAGE, SYM, Card({"C100.TXT": "display=off\nled=off\n"}))
    m.task("periph")
    m.call_arm(A_GPIO_SET, 43, 1)  # stock boot drives the LED pins before the mount
    m.call_arm(A_GPIO_SET, 95, 1)
    mount(m)
    h.ok(not m.calls("ht_cmd"), "nothing on the HT1621 bus from the mount (storage) task", f"({m.calls('ht_cmd')})")
    h.eq(m.gpio(43), 1, "LED pins untouched from the storage task")
    m.reset()
    tick(m)
    cmds = [t[1] for t in m.calls("ht_cmd")]
    h.eq(cmds, [0x02], "first tick sends LCD OFF once")
    h.eq(cfgv(m, CFG_DISPLAY), 0, "cfg.display 0")
    h.eq(cfgv(m, CFG_LED), 0, "cfg.led 0")
    h.eq(m.gpio(43), 0, "GPIO 43 driven to its off level")
    h.eq(m.gpio(95), 0, "GPIO 95 driven to its off level")
    m.reset()
    tick(m, 300)
    h.eq(m.calls("ht_cmd"), [], "second tick: no repeat")


def t_bad_keys(h):
    h.section = "bad keys"
    print("\nunknown and malformed keys are skipped")
    m = boot("log=on\ndisplay=banana\nnonsense\nled\n=off\nled=on\nusb_meter=floppy\n")
    tick(m)
    h.eq(cfgv(m, CFG_LED), 1, "last good led=on wins")
    h.eq(cfgv(m, CFG_DISPLAY), 1, "display=banana leaves the default")
    h.eq(cfgv(m, CFG_USB + 2), 2, "usb_meter=floppy leaves the default")
    h.has("bad display=banana", m=m)
    h.has("bad nonsense", m=m)


# ---------------------------------------------------------------- log
def t_log_capture(h):
    h.section = "printf capture"
    print("\nprintf capture to LOG.TXT, through the real stock printf")
    m = boot()
    tick(m)
    m.task("ap")
    fmt = m.strp("photo-capture-done state=%#x\r\n")
    m.reset()
    m.call_arm(A_PRINTF, fmt, 0x102)
    h.eq(m.hits["logcap"], 1, "printf reached logcap through the 0x537b4 hook")
    tick(m, 2500)
    h.has("photo-capture-done state=0x102\r\n", m=m)
    n = m.names()
    h.ok(
        "write" in n and "flush" in n and n.index("flush") > n.index("write"),
        "drive flushed after the append",
        f"({n})",
    )
    h.clean(m)

    print("  log=off")
    m = boot("log=off\n")
    m.task("ap")
    m.call_arm(A_PRINTF, m.strp("stock-line-dropped\r\n"))
    tick(m)
    h.ok("stock-line-dropped" not in log(m), "log=off drops stock prints")
    h.ok("LOG.TXT" not in m.card.names(), "log=off writes no LOG.TXT at all", f"({m.card.names()})")

    print("  before C100.TXT is read, prints go to the UART only")
    m = Machine(IMAGE, SYM, Card({"C100.TXT": "log=on\n"}))
    m.task("ap")
    m.call_arm(A_PRINTF, m.strp("early-boot-line\r\n"))
    h.eq(m.hits["logcap"], 0, "disarmed: logcap not entered")
    h.eq(
        "".join(chr(t[1]) for t in m.calls("uart")), "early-boot-line\r\n", "disarmed: stock UART output gets the line"
    )
    mount(m)
    m.poke8(B_USB_POLL_EN, 1)
    tick(m)
    h.ok("early-boot-line" not in log(m), "and it never reaches LOG.TXT")


def t_log_bounds(h):
    h.section = "capture bounds"
    print("\ncapture bounds")
    m = boot()
    tick(m)
    before = m.ring()
    m.put(A_PRINT_BUF, b"\0")
    m.call("logcap", A_PRINT_BUF)
    h.eq(m.ring() - before, 0, "empty line adds nothing")
    m.put(A_PRINT_BUF, b"x" * 600)  # no NUL inside PRINT_BUF's 512 B
    m.call("logcap", A_PRINT_BUF)
    h.eq(m.ring() - before, 511, "unterminated line bounded at 511")


# ---------------------------------------------------------------- CMD.TXT
def t_cmd(h):
    h.section = "CMD.TXT"
    print("\nCMD.TXT is read, deleted, then run")
    m = boot(cmd="sound off\ndisplay off\n")
    tick(m)
    h.ok("CMD.TXT" not in m.card.names(), "CMD.TXT deleted", f"(card {m.card.names()})")
    n = m.names()
    iu = n.index("unlink") if "unlink" in n else -1
    h.ok(
        0 <= iu < len(n) - 1
        and "flush" in n[iu:]
        and "ht_cmd" in n[iu:]
        and n.index("flush", iu) < n.index("ht_cmd", iu),
        "unlink, then flush, before running it",
        f"({n})",
    )
    h.eq([t[1] for t in m.calls("ht_cmd")], [0x02], "display off sent LCD OFF")
    h.eq(leaks(m), {}, "CMD.TXT buffer freed")
    tick(m, 2100)
    h.has("[v5] sound off", "command output logged on the next flush", m=m)

    print("  poke, call, msg, key, peek and log are rejected")
    probe = 0xE3388  # a .data word that is not 0x1234
    m = boot(cmd=f"poke {probe:#x} 0x1234\ncall 0x1fbac\nmsg 0x7007\nkey shutter\npeek 0xe6968 4\nlog hi\n")
    before = m.u32(probe)
    tick(m)
    tick(m, 2100)
    h.eq(m.u32(probe), before, "poke stores nothing")
    h.not_called("reboot", "call does not reach 0x1fbac", m=m)
    h.eq(m.calls("apq"), [], "msg and key post nothing")
    for c in ("poke", "call", "msg", "key", "peek", "log"):
        h.has(f"cmd: bad {c}", m=m)
    h.ok("000e6968:" not in log(m), "peek dumps nothing")

    print("  a CMD.TXT that cannot be deleted is not run (it would run on every poll)")
    m = boot(cmd="display off\nreboot\n")
    allow_reboot(m)
    m.unlink_ret = -1
    tick(m)
    h.eq(m.calls("ht_cmd"), [], "display off not run")
    h.eq(m.calls("apq"), [], "reboot not posted")
    h.not_called("unmount", "card not unmounted for a reboot", m=m)
    h.eq(leaks(m), {}, "CMD.TXT buffer freed")
    tick(m, 2100)
    h.has("cmd: rm fail", m=m)

    m = boot(cmd="sound off\n", files={"GP_CARDVR_UPGRADE.BIN": b"fw"})
    tick(m)
    h.ok("CMD.TXT" in m.card.names(), "left alone while a firmware update is on the card")
    m = boot("cmd=off\n", cmd="sound off\n")
    tick(m)
    h.ok("CMD.TXT" not in m.card.names(), "cmd is not a key: CMD.TXT runs anyway")

    m = boot()
    tick(m)
    m.card.f["CMD.TXT"] = bytearray(b"sound off\n")
    tick(m, 1000)
    h.ok("CMD.TXT" in m.card.names(), "polled at most every 3 s")
    tick(m, 2500)
    h.ok("CMD.TXT" not in m.card.names(), "picked up on the next poll")
    m.card.f["CMD.TXT"] = bytearray(b"x" * 3000)
    tick(m, 3100)
    h.ok("CMD.TXT" not in m.card.names(), "oversized CMD.TXT deleted too")
    tick(m, 2100)
    h.has("cmd: bad file", m=m)


def t_reboot(h):
    h.section = "reboot order"
    print("\nreboot order: log out, card unmounted and checked, then 0x7007 urgent")
    m = boot(cmd="reboot\n")
    allow_reboot(m)
    tick(m, 5000)
    tr = m.trace
    names = m.names()
    h.ok("unmount" in names, "drive unmounted")
    if "unmount" not in names:
        return
    iu = names.index("unmount")
    h.eq(tr[iu][1], 6, "unmount(6)")
    w = [i for i, t in enumerate(tr) if t[0] == "write"]
    h.ok(w and w[-1] < iu, "LOG.TXT appended before the unmount")
    h.ok("flush" in names[w[-1] : iu] if w else False, "and flushed before the unmount")
    h.has("[v5] reboot\r\n", "the reboot line itself reached the card", m=m)
    posts = [(i, t) for i, t in enumerate(tr) if t[0] == "apq"]
    h.eq([t[2] for _, t in posts], [0x7007], "one post: 0x7007")
    if posts:
        i, t = posts[0]
        h.ok(i > iu, "posted after the unmount")
        h.eq(t[1], APQ_OBJ, "to the app queue")
        h.eq(t[6], 1, "urgent")
        h.eq(t[5], 0x28, "ApQ item size")
    h.ok(
        not any(t[0] in ("open", "write", "stat", "unlink", "rename") for t in tr[iu:]),
        "no file call after the unmount",
    )
    h.eq(stv(m, ST_STALE), 1, "card marked stale while waiting for the power cut")
    h.clean(m)

    print("  unmount fails (a file is open)")
    m = boot(cmd="reboot\n")
    allow_reboot(m)
    m.unmount_ret = -1
    tick(m, 5000)
    h.ok("unmount" in m.names(), "unmount tried")
    h.eq([t[2] for t in m.calls("apq")], [], "nothing posted")
    tick(m, 2500)
    h.has("reboot ffffffff", "failure logged", m=m)

    print("  refused while busy")
    for name, addr, val, wide in (
        ("capture busy", B_CAPTURE_BUSY, 1, False),
        ("recording", B_REC_FLAGS, 2, False),
        ("record transition", B_REC_FLAGS, 8, False),
        ("startup state", W_APP_STATE, 0x100, True),
    ):
        m = boot(cmd="reboot\n")
        allow_reboot(m)
        (m.poke16 if wide else m.poke8)(addr, val)
        tick(m, 5000)
        h.ok("unmount" not in m.names() and not m.calls("apq"), f"{name}: no unmount, no post", f"({m.names()})")

    print("  the app state is a halfword (stock: ldrh/strh only); the halfword above it does not count")
    m = boot(cmd="reboot\n")
    allow_reboot(m)
    m.poke16(W_APP_STATE + 2, 0xBEEF)
    tick(m, 5000)
    h.eq([t[2] for t in m.calls("apq")], [0x7007], "state 0x102 with 0xbeef above it: reboot posted")


# ---------------------------------------------------------------- sound
def t_sound(h):
    h.section = "sound gate"
    print("\nsound gate (C side)")
    on = boot("sound=on\n")
    h.eq(on.call("act_sound_gate", 1), 1, "id 1 plays when sound=on")
    off = boot("sound=off\n")
    h.eq(off.call("act_sound_gate", 1), 0, "id 1 blocked when sound=off")
    h.eq(off.call("act_sound_gate", 0), 0, "click blocked when sound=off")

    print("  power-on chime parked by the disarmed stub until C100.TXT is known")
    for cfg, plays in (("sound=on\n", True), ("sound=off\n", False)):
        m = Machine(IMAGE, SYM, Card({"C100.TXT": cfg}))
        m.task("ap")
        h.eq(m.call_arm(0x22A80, 2, sp=STACK - 0x200), 0xFFFFFFFF, f"{cfg.strip()}: chime 2 returns -1 before settings")
        h.eq(stv(m, ST_SOUND), 2, f"{cfg.strip()}: and is parked in sound_pending")
        h.eq(m.call_arm(0x22A80, 7, sp=STACK - 0x200), 0, f"{cfg.strip()}: id 7 plays as stock")
        h.eq([t[1] for t in m.calls("play")], [7], f"{cfg.strip()}: only id 7 reached the body")
        h.not_called("act_sound_gate", f"{cfg.strip()}: no C before settings", m=m)
        mount(m)
        m.reset()
        tick(m)
        played = [t[1] for t in m.calls("play")]
        h.eq(played, [2] if plays else [], f"{cfg.strip()}: chime {'replayed' if plays else 'dropped'}")
        h.eq(stv(m, ST_SOUND), 0, "sound_pending cleared")


def t_hook_sound(h):
    h.section = "seam S1 0x22A80"
    print("\nseam S1: play_effect entry 0x22A80")
    sp = STACK - 0x200
    off = boot("sound=off\n")
    off.task("ap")
    g = off.watch_writes(GPIO + 0x40, GPIO + 0x60)  # bank 2 holds GPIO 44
    r = off.call_arm(0x22A80, 1, sp=sp)
    h.eq(r, 0xFFFFFFFF, "blocked id returns -1")
    h.eq(off.reg(UC_ARM_REG_SP), sp, "sp intact")
    h.called("act_sound_gate", "went through act_sound_gate", m=off)
    h.not_called("play", "play_effect body never entered", m=off)
    h.eq(g, [], "no write to GPIO 44's bank")
    h.eq(off.gpio(44), 0, "GPIO 44 low")

    on = boot("sound=on\n")
    on.task("ap")
    r4 = 0x44444444
    on.call_arm(0x22A80, 5, sp=sp, regs={UC_ARM_REG_R4: r4}, until=0x22A84)
    h.eq(on.reg(UC_ARM_REG_R0), 5, "allowed id falls through to 0x22a84 with r0 = id")
    h.eq(on.reg(UC_ARM_REG_SP), sp - 20, "with the displaced push {r4-r7,lr} done")
    h.eq(on.u32(sp - 4), RET, "pushed lr is the caller's return address")
    h.eq(on.u32(sp - 20), r4, "pushed r4 is the caller's r4")
    on.reset()
    r = on.call_arm(0x22A80, 5, sp=sp)
    h.eq(r, 0, "and play_effect returns 0")
    h.eq([t[1] for t in on.calls("play")], [5], "body ran once with id 5")
    h.eq(on.gpio(44), 1, "amp opened by the body")
    h.eq(on.reg(UC_ARM_REG_SP), sp, "sp intact after the full call")


# ---------------------------------------------------------------- LED
def t_led_gate(h):
    h.section = "LED gate L1"
    print("\nLED gate at gpio_set 0x433F4")
    m = boot("led=on\n")
    tick(m)
    m.call_arm(A_GPIO_SET, 43, 1)
    h.eq(m.gpio(43), 1, "led=on passes 43 high through")
    m.call_arm(A_GPIO_SET, 43, 0)
    h.eq(m.gpio(43), 0, "led=on passes 43 low through")
    off = boot("led=off\n")
    tick(off)
    off.call_arm(A_GPIO_SET, 43, 1)
    h.eq(off.gpio(43), 0, "led=off holds 43 low when stock asks for high")
    off.call_arm(A_GPIO_SET, 95, 1)
    h.eq(off.gpio(95), 0, "led=off holds 95 low")
    h.eq(off.u8(off.sym["led"] + LED_WANT95), 1, "and remembers stock wanted 95 high")
    off.call_arm(A_GPIO_SET, 44, 1)
    h.eq(off.gpio(44), 1, "other pins untouched")
    sp = STACK - 0x80
    off.call_arm(A_GPIO_SET, 43, 1, sp=sp)
    h.eq(off.reg(UC_ARM_REG_SP), sp, "gpio_set returns with sp intact")
    print("  first mount: want43/want95 start from what stock left on the pins")
    m = Machine(IMAGE, SYM, Card({"C100.TXT": "led=on\n"}))
    m.gpio_input(43, 0)
    m.gpio_input(95, 1)
    mount(m)
    h.eq((m.u8(m.sym["led"] + LED_WANT43), m.u8(m.sym["led"] + LED_WANT95)), (0, 1), "led_init read 43 low and 95 high")
    m.gpio_input(95, 0)
    mount(m)
    h.eq(m.u8(m.sym["led"] + LED_WANT95), 1, "a later mount leaves them to the gate")
    print("  led back on restores what stock last asked for")
    off.call("cmd_run", off.strp("led on"), 6)
    tick(off, 300)
    h.eq((off.gpio(43), off.gpio(95)), (1, 1), "43 and 95 back to stock's levels")


def t_get95(h):
    h.section = "get95 L2/L3"
    print("\nUSB busy read sees what stock asked for")
    m = boot("led=off\n")
    tick(m)
    m.call_arm(A_GPIO_SET, 95, 1)  # stock says capture started
    m.gpio_input(95, 0)
    h.eq(m.call("v5_get95", 95), 1, "get95 reports the stock level, not the pin")
    on = boot("led=on\n")
    tick(on)
    on.gpio_input(95, 1)
    h.eq(on.call("v5_get95", 95), 1, "led=on reads the real pin (high)")
    on.gpio_input(95, 0)
    h.eq(on.call("v5_get95", 95), 0, "led=on reads the real pin (low)")


# ---------------------------------------------------------------- long press
def t_long_select(h):
    h.section = "long select"
    print("\nlong press on Select toggles display and LED")
    m = boot("display=on\nled=on\n")
    tick(m)
    m.call("act_long_select")
    h.eq(stv(m, ST_TOGGLE), 1, "toggle request set")
    tick(m, 300)
    h.eq(cfgv(m, CFG_DISPLAY), 0, "display off")
    h.eq(cfgv(m, CFG_LED), 0, "led off")
    h.eq(stv(m, ST_TOGGLE), 0, "request cleared")
    m.call("act_long_select")
    tick(m, 300)
    h.eq(cfgv(m, CFG_DISPLAY), 1, "and back on")
    m.poke8(B_F_HELD, 1)
    m.call("act_long_select")
    h.eq(stv(m, ST_TOGGLE), 0, "repeat calls of the same hold ignored")


def t_hook_k1(h):
    h.section = "seam K1 0x1dda4"
    print("\nseam K1: Select handler 0x1dda4")
    sp = STACK - 0x200
    m = boot("display=on\nled=on\n")
    tick(m)
    m.poke8(B_F_HELD, 0)
    m.reset()
    # 0x1dd9c cmp r5,#0x18; 0x1dda0 strb r4,[r3]; 0x1dda4 bhi
    m.call_arm(0x1DD9C, sp=sp, regs={UC_ARM_REG_R5: 0x1C, UC_ARM_REG_R4: 0, UC_ARM_REG_R3: 0x12170A}, until=0x1DDB8)
    h.called("act_long_select", "count 0x1c reaches act_long_select", m=m)
    h.eq(m.reg(UC_ARM_REG_SP), sp, "ends at 0x1ddb8 with sp as found")
    h.eq(stv(m, ST_TOGGLE), 1, "toggle requested")
    tick(m, 300)
    h.eq((cfgv(m, CFG_DISPLAY), cfgv(m, CFG_LED)), (0, 0), "next tick turns display and LED off")
    m.reset()
    m.call_arm(0x1DD9C, sp=sp, regs={UC_ARM_REG_R5: 0x10, UC_ARM_REG_R4: 0, UC_ARM_REG_R3: 0x12170A}, until=0x1DDA8)
    h.not_called("act_long_select", "short count falls through to 0x1dda8 untouched", m=m)


# ---------------------------------------------------------------- USB
def plug(m, sp=STACK - 0x200):
    """G1: VBUS state 0 with an insert edge, entered at 0x1f5b0, lands on 0x1f68c."""
    m.task("periph")
    regs = {r: 0x1000 + i for i, r in enumerate(CALLEE_SAVED)}
    m.call_arm(0x1F5B0, sp=sp, regs=regs, until=0x1F68C)
    return all(m.reg(r) == v for r, v in regs.items()) and m.reg(UC_ARM_REG_SP) == sp


def t_usb_plugin(h):
    h.section = "USB personality"
    print("\npersonality per switch position, through G1 0x1f5b0")
    for mode, want, name in ((0, 0x8001, "photo=drive"), (1, 0x8003, "video=webcam"), (2, 0x8003, "metering=webcam")):
        m = boot(CFG_DEFAULT, mode=mode)
        m.gpio_input(95, 0)
        intact = plug(m)
        posts = [(t[1], t[2]) for t in m.calls("qsend")]
        h.eq(posts, [(USBQ_H, want)], f"{name} posts {want:#x} to the USB queue")
        h.ok(intact, f"{name}: lands on 0x1f68c with sp and r4-r11 intact")
    m = boot("usb_video=drive\n", mode=1)
    plug(m)
    h.eq([t[2] for t in m.calls("qsend")], [0x8001], "usb_video=drive honoured")
    h.called("flush", "drive: camera cache flushed before the host reads the card", m=m)

    print("  webcam start clears the SET_CONFIG callback a drive init left behind")
    for mode, want in ((1, 0), (0, 0x3E5AC)):
        m = boot(CFG_DEFAULT, mode=mode)
        m.gpio_input(95, 0)
        m.poke32(W_MSDC_CB, 0x3E5AC)  # as usb_msdc_init leaves it (0x3ea68 -> 0x4d65c)
        plug(m)
        h.eq(m.u32(W_MSDC_CB), want, f"mode {mode}: [0x124294] = {want:#x}")

    print("  unplug through G2 0x1f530")
    for mode, want, off in ((0, [], True), (1, [0x8005], False)):
        m = boot(CFG_DEFAULT, mode=mode)
        plug(m)
        m.reset()
        m.call_arm(0x1F530, sp=STACK - 0x200, until=0x1F534)
        h.eq([t[2] for t in m.calls("qsend")], want, f"mode {mode}: unplug posts {want}")
        h.eq("usb_off" in m.names(), off, f"mode {mode}: stock 0xbdf04 {'runs' if off else 'skipped'}")
        h.eq(stv(m, ST_STALE), 0, f"mode {mode}: card not stale")
        h.eq(m.reg(UC_ARM_REG_SP), STACK - 0x200, f"mode {mode}: returns to 0x1f534 with sp intact")
    m = boot()
    m.call_arm(0x1F530, sp=STACK - 0x200, until=0x1F534)
    h.called("usb_off", "unplug with nothing posted falls back to 0xbdf04", m=m)

    print("  charger unplug in the drive personality: the camera keeps the card")
    m = boot(CFG_DEFAULT, mode=0)
    m.gpio_input(95, 0)
    plug(m)
    m.task("periph")
    h.eq(m.call("fs_ready"), 0, "fs_ready() 0 while the drive personality is up")
    m.call_arm(0x1F530, sp=STACK - 0x200, until=0x1F534)
    h.eq(m.call("fs_ready"), 1, "fs_ready() 1 after the unplug")
    m.task("ap")
    m.call_arm(A_PRINTF, m.strp("after-unplug\r\n"))
    m.reset()
    tick(m, 2100)
    h.not_called("sd_init", "no SD re-init", m=m)
    h.not_called("mount", "no remount", m=m)
    h.has("after-unplug", "LOG.TXT flushes as before", m=m)
    h.clean(m)


def t_usb_busy_defers(h):
    h.section = "USB deferral"
    print("\nplug-in while capturing defers to the tick (G3b 0x2247C)")
    m = boot(CFG_DEFAULT, mode=1)
    m.gpio_input(95, 1)
    plug(m)
    h.not_called("qsend", "nothing posted while the busy pin is high", m=m)
    h.eq(stv(m, ST_PENDING), 2, "want stashed in usb_pending")
    m.gpio_input(95, 0)
    m.poke8(B_USB_STATE, 1)
    m.poke8(B_MODE, 2)
    m.reset()
    m.call_arm(0x2247C, sp=STACK - 0x104, until=0x22480)
    n = m.names()
    h.ok(
        "vbus" in n and "v5_usb_tick" in n and n.index("vbus") < n.index("v5_usb_tick"),
        "stock VBUS poll first, then v5_usb_tick",
        f"({n})",
    )
    h.eq([t[2] for t in m.calls("qsend")], [0x8003], "tick posts once the bus is free")
    h.eq(m.reg(UC_ARM_REG_SP), STACK - 0x104, "returns to 0x22480 with sp intact")

    print("  plug-in before the first mount result")
    m = boot(mode=0)
    m.poke8(B_USB_POLL_EN, 0)
    plug(m)
    h.not_called("qsend", "deferred until the mount result is in", m=m)
    m.poke8(B_USB_POLL_EN, 1)
    m.poke8(B_USB_STATE, 1)
    m.call_arm(0x2247C, sp=STACK - 0x100, until=0x22480)
    h.eq([t[2] for t in m.calls("qsend")], [0x8001], "then posted by the tick")


def t_eject_cb(h):
    h.section = "eject callback"
    print("\neject callback notes the handback")
    m = boot(CFG_DEFAULT)
    m.task("usb")
    seen = []
    m.uc.hook_add(
        UC_HOOK_CODE,
        lambda uc, a, size, _: seen.append((stv(m, ST_EJECTING), stv(m, ST_RELEASED))),
        begin=A_EJECT,
        end=A_EJECT,
    )
    m.call("eject_stub", 0)
    h.called("v5_eject_cb", "armed, eject_stub goes on to v5_eject_cb", m=m)
    h.eq(seen, [(1, 0)], "ejecting set before the stock uninit runs")
    h.eq((stv(m, ST_EJECTING), stv(m, ST_RELEASED)), (0, 1), "then host_released set, ejecting cleared")
    h.called("eject", "stock 0xbe2d4 still runs", m=m)
    h.ok(
        not any(t[0] in ("open", "write", "stat", "unlink", "mount", "sd_init") for t in m.trace),
        "no file I/O in the USB task",
    )


def t_hook_eject(h):
    h.section = "seam eject e2e"
    print("\nseam: eject handback end to end, stock 0x4e8dc -> eject_stub -> v5_eject_cb -> tick -> LUN 1")
    m = boot(CFG_DEFAULT, mode=0)
    m.gpio_input(95, 0)
    plug(m)
    h.eq(stv(m, ST_ACTIVE), 1, "drive personality active")
    m.task("ap")
    m.call_arm(A_PRINTF, m.strp("while-host-owns\r\n"))
    m.card.f["CMD.TXT"] = bytearray(b"sound off\n")
    m.reset()
    tick(m, 3100)
    h.ok(
        not any(t[0] in ("open", "write", "stat", "unlink") for t in m.trace),
        "no card I/O while the host owns the drive",
        f"({m.names()})",
    )
    m.task("usb")
    r = m.call_arm(A_LUN_REG, 1, 0, 0, SD_OPS)  # usb_msdc_init registers LUN 0 (0x3ea04)
    h.eq(r, 0, "stock LUN register 0x4ea70 ok")
    h.eq(m.u32(LUN0 + 0x40), m.sym["eject_stub"], "LUN+0x40 holds the patched G6 pointer (eject_stub, ARM)")
    h.eq(m.u16(H_LUN0), 1, "LUN 0 status 1")
    # the host writes CMD.TXT while it owns the card, then ejects
    m.card.f["CMD.TXT"] = bytearray(b"set sound=off\n")
    m.poke32(MSC_CTX + 4, LUN0)
    m.poke8(MSC_CTX + 0x51, 0)
    m.poke8(MSC_CTX + 0x57, 2)  # START STOP UNIT, CDB[4] LoEj
    m.reset()
    m.call_arm(A_START_STOP)
    h.called("v5_eject_cb", "0x4e914 blx reached v5_eject_cb through eject_stub", m=m)
    h.called("eject", "which ran the stock 0xbe2d4 (SD uninit)", m=m)
    h.eq(m.u16(H_LUN0), 0xFFAA, "LUN 0 reads 0xffaa (no medium)")
    h.eq(stv(m, ST_RELEASED), 1, "host_released set")
    m.task("periph")
    h.eq(m.call("fs_ready"), 0, "fs_ready() 0 while host_released is set")

    lun = m.watch_writes(LUN0, LUN0 + 0x44)
    m.reset()
    tick(m, 4000)
    n = m.names()
    h.ok(
        "sd_init" in n
        and "mount" in n
        and n.index("sd_init") < n.index("mount")
        and n.index("mount") < n.index("open"),
        "tick: sd_init, mount, then card I/O",
        f"({n})",
    )
    h.ok("CMD.TXT" not in m.card.names(), "CMD.TXT the host wrote was run and deleted")
    h.has("[v5] sound=off", "CMD.TXT output flushed before the handback", m=m)
    h.eq(stv(m, ST_RELEASED), 0, "host_released cleared")
    h.eq(lun, [], "LUN untouched until the handback settle time")
    h.eq(m.u16(H_LUN0), 0xFFAA, "host still sees no medium")
    m.reset()
    tick(m, 2100)
    h.eq(
        [(a, s, v) for a, s, v, _ in lun], [(H_LUN0, 2, 1)], "handback: one 16-bit write of 1 to 0x1242b8, nothing else"
    )
    h.has("[v5] eject\r\n", m=m)
    h.has("[v5] handback", "log flushed before the LUN goes back", m=m)
    n = m.names()
    h.ok("write" in n and n[-1] not in ("open", "write"), "no card I/O left after the handback")
    lunlen = len(lun)
    h.eq(bytes(m.uc.mem_read(LUN0 + 0xC, 4)).hex(), "01000101", "LUN +0xe/+0xf (ready, type) kept")
    tick(m, 300)
    h.eq(len(lun), lunlen, "no further LUN writes")
    h.clean(m, "no file call while the SD driver was down")

    print("  remount fails after the eject")
    m = boot(CFG_DEFAULT, mode=0)
    m.gpio_input(95, 0)
    plug(m)
    m.task("usb")
    m.call_arm(A_LUN_REG, 1, 0, 0, SD_OPS)
    m.poke32(MSC_CTX + 4, LUN0)
    m.poke8(MSC_CTX + 0x51, 0)
    m.poke8(MSC_CTX + 0x57, 2)
    m.call_arm(A_START_STOP)
    lun = m.watch_writes(LUN0, LUN0 + 0x44)
    m.mount_ret = -1
    m.reset()
    tick(m, 4000)
    tick(m, 2100)
    h.eq(stv(m, ST_STALE), 1, "card stays stale")
    h.eq(lun, [], "LUN not handed back over a card the camera could not mount")
    h.ok(not any(t[0] in ("open", "write", "stat", "unlink") for t in m.trace), "no card I/O")
    m.mount_ret = 0
    m.poke8(B_CAPTURE_BUSY, 1)
    m.reset()
    tick(m, 5100)
    h.ok(not {"sd_init", "mount"} & set(m.names()), "no retry while a capture is busy", f"({m.names()})")
    m.poke8(B_CAPTURE_BUSY, 0)
    tick(m, 5100)
    tick(m, 2100)
    h.eq(stv(m, ST_STALE), 0, "retry remounts")
    h.eq([(a, s, v) for a, s, v, _ in lun], [(H_LUN0, 2, 1)], "then hands the LUN back")

    print("  retries stop after 6, stock's next mount clears the stale card")
    m = boot(CFG_DEFAULT)
    m.poke8(m.sym["st"] + ST_STALE, 1)
    m.mount_ret = -1
    m.reset()
    for _ in range(12):
        tick(m, 5100)
    h.eq(len(m.calls("mount")), 6, "6 remount attempts, then none")
    mount(m)
    h.eq(stv(m, ST_STALE), 0, "load_settings on stock's mount clears card_stale")
    m.mount_ret = 0
    m.reset()
    tick(m, 5100)
    tick(m, 5100)
    h.not_called("mount", "and nothing is retried after it", m=m)


# ---------------------------------------------------------------- remount retries
REMOUNT_CALLS = ("fs_take", "sd_init", "fs_give", "mount", "unmount", "sd_uninit")


def t_remount(h):
    h.section = "remount recovery"
    print("\nremount after an eject: one stock mount attempt per try, stock's recovery in between")

    def ejected(m):
        m.poke8(m.sym["st"] + ST_ACTIVE, 1)
        m.poke8(m.sym["st"] + ST_RELEASED, 1)
        m.poke16(H_LUN0, 0xFFAA)
        m.sd_up = m.mounted = False

    def attempt(m, ms):
        m.reset()
        tick(m, ms)
        return [x for x in m.names() if x in REMOUNT_CALLS]

    m = boot(CFG_DEFAULT)
    ejected(m)
    m.sd_init_ret = -1
    h.eq(
        attempt(m, 4000), ["fs_take", "sd_init", "fs_give", "sd_uninit"], "SD init fails: SD uninit, no mount (0x3dd3c)"
    )
    h.eq(stv(m, ST_STALE), 1, "card stale")
    h.eq(attempt(m, 1100), ["fs_take", "sd_init", "fs_give", "sd_uninit"], "next try: SD init again, uninit again")
    m.sd_init_ret = 0
    h.eq(attempt(m, 5100), ["fs_take", "sd_init", "fs_give", "mount"], "SD init works: mount")
    h.eq((stv(m, ST_STALE), m.u8(B_CARD_OK)), (0, 1), "card back: not stale, card flag 1")
    h.clean(m)

    m = boot(CFG_DEFAULT)
    ejected(m)
    m.mount_ret = -1
    h.eq(
        attempt(m, 4000),
        ["fs_take", "sd_init", "fs_give", "mount", "unmount", "sd_uninit"],
        "mount fails: unmount and SD uninit (0x3dba4..0x3dbb0)",
    )
    m.mount_ret = 0
    h.eq(attempt(m, 1100), ["mount"], "next try: the mount alone, as stock retries it (0x3dbc0)")
    h.eq((stv(m, ST_STALE), m.u8(B_CARD_OK)), (0, 1), "card back")
    tick(m, 2100)  # the handback hands LUN 0 to the host again; only then can it eject again
    h.eq(m.u16(H_LUN0), 1, "LUN 0 handed back")
    ejected(m)
    h.eq(attempt(m, 4000), ["fs_take", "sd_init", "fs_give", "mount"], "a later eject starts from SD init again")
    h.clean(m)


# ---------------------------------------------------------------- cards
def t_no_card(h):
    h.section = "no card"
    print("\nno card: settings never load, the patch stays disarmed and the camera runs as the base image")
    m = Machine(IMAGE, SYM, Card({}), present=False)
    m.poke8(B_MOUNT_FAIL, 1)  # 0x2e9f8, photo mount-fail handler
    m.poke8(B_UPD_CHECKED, 1)  # 0x3e580 runs after a failed mount too
    m.poke8(B_USB_POLL_EN, 0)
    m.poke16(W_APP_STATE, 0x102)
    tick(m, 5000)
    tick(m, 5000)
    h.eq(stv(m, ST_BOOTED), 0, "booted stays 0")
    h.ok(not any(t[0] in ("open", "stat", "write", "unlink", "mount") for t in m.trace), "no file call")
    m.gpio_input(95, 0)
    plug(m)
    h.eq([t[2] for t in m.calls("qsend")], [0x8003], "plug-in posts the base image's webcam init (pool word 0x8003)")
    m.task("ap")
    m.call_arm(0x22A80, 1, sp=STACK - 0x200)
    h.eq([t[1] for t in m.calls("play")], [1], "sounds play as stock")
    h.eq({n: m.hits[n] for n in V5_ENTRIES if m.hits[n]}, {}, "no payload C entered")
    h.clean(m)


def t_empty_card(h):
    h.section = "empty card"
    print("\nempty card: defaults, logging off, so no LOG.TXT")
    m = boot(None)
    tick(m, 5000)
    tick(m, 5000)
    h.eq(stv(m, ST_BOOTED), 1, "booted flag set")
    h.ok("LOG.TXT" not in m.card.names(), "no LOG.TXT with the defaults", f"({m.card.names()})")
    h.eq([cfgv(m, i) for i in range(7)], [1, 1, 1, 0, 1, 2, 2], "defaults: log off")
    h.clean(m)


# ---------------------------------------------------------------- seams: LOG, D1, SET
def t_hook_chain(h):
    h.section = "seam LOG/D1/SET"
    print("\nseam LOG: 0x537b4 with r4 = PRINT_BUF")
    m = boot()
    tick(m)
    sp = STACK - 0x200
    line = b"seam-line-537b4\r\n"
    m.put(A_PRINT_BUF, line + b"\0")
    before = m.ring()
    m.reset()
    m.call_arm(0x537B4, sp=sp, regs={UC_ARM_REG_R4: A_PRINT_BUF}, until=0x5377C)
    got_sp = m.reg(UC_ARM_REG_SP)
    h.eq(m.calls("logcap")[:1] and m.calls("logcap")[0][1], A_PRINT_BUF, "logcap entered with r0 = PRINT_BUF")
    h.eq(got_sp, sp, "returns to 0x5377c with sp intact")
    h.eq(m.ring() - before, len(line), "line queued")
    # the rest of printf's epilogue with a real frame: 0xc locals, r4 r5 lr, 4 varargs
    m.poke32(sp + 0xC, 0x4444)
    m.poke32(sp + 0x10, 0x5555)
    m.poke32(sp + 0x14, RET)
    m.call_arm(0x5377C, sp=sp, lr=0)
    h.eq((m.reg(UC_ARM_REG_SP), m.reg(UC_ARM_REG_R4)), (sp + 0x28, 0x4444), "printf's epilogue then pops its own frame")
    m.reset()
    m.call_arm(A_PRINTF, m.strp("whole-printf %d\r\n"), 42, sp=sp)
    h.eq(m.reg(UC_ARM_REG_SP), sp, "a whole stock printf call returns with sp intact")
    m.call("log_flush")
    h.has("whole-printf 42", m=m)

    print("seam D1: 0x22360 in the peripheral task")
    m = boot()
    regs = {r: 0x2000 + i for i, r in enumerate(CALLEE_SAVED)}
    for sp in (STACK - 0x100, STACK - 0x104):
        m.reset()
        m.call_arm(0x22360, sp=sp, regs=regs, until=0x22364)
        n = m.names()
        h.ok(
            "seg_refresh" in n and "v5_tick" in n and n.index("seg_refresh") < n.index("v5_tick"),
            f"sp%8={sp % 8}: seg_refresh 0x21318, then v5_tick",
            f"({n[:6]})",
        )
        h.eq(m.sp_at.get("v5_tick", 1) % 8, 0, f"sp%8={sp % 8}: v5_tick entered 8-aligned")
        h.eq(m.reg(UC_ARM_REG_SP), sp, f"sp%8={sp % 8}: back at 0x22364 with sp intact")
        h.ok(all(m.reg(r) == v for r, v in regs.items()), f"sp%8={sp % 8}: r4-r11 intact")

    print("seam SET: 0x37fa0 at the VERSION.TXT writer's epilogue")
    m = Machine(IMAGE, SYM, Card({"C100.TXT": "led=off\n"}))
    m.task("storage")
    sp = STACK - 0x200
    m.poke32(sp + 0x58, 0xCAFE0004)
    m.poke32(sp + 0x5C, RET)
    stops = {}

    def at_ret(uc, a, size, _):
        stops.setdefault("sp", uc.reg_read(UC_ARM_REG_SP))

    m.uc.hook_add(UC_HOOK_CODE, at_ret, begin=0x37FA4, end=0x37FA4)
    m.call_arm(0x37FA0, sp=sp, regs={UC_ARM_REG_R4: 0x1234})
    h.called("load_settings", "load_settings ran", m=m)
    h.eq(m.sp_at.get("load_settings", 0) % 8, 0, "load_settings entered 8-aligned")
    h.eq(stops.get("sp"), sp + 0x58, "reached 0x37fa4 after the displaced add sp,sp,#0x58")
    h.eq(m.reg(UC_ARM_REG_R4), 0xCAFE0004, "0x37fa4 popped the caller's r4")
    h.eq(m.reg(UC_ARM_REG_SP), sp + 0x60, "and returned to the caller with sp balanced")
    h.eq(cfgv(m, CFG_LED), 0, "settings applied")


# ---------------------------------------------------------------- seam: re-entrancy
def t_reentry(h):
    h.section = "seam re-entrancy"
    print("\nseam: a stock print arriving during a flush")
    m = boot()
    tick(m)
    m.task("periph")
    m.call("v5_log", m.strp("something-to-flush"))
    m.inject(m.sym["fs_append"], A_PRINTF, args=(m.strp("printed-during-flush\r\n"),))
    m.reset()
    tick(m, 2100)
    h.has("something-to-flush", m=m)
    h.ok("printed-during-flush" not in log(m), "a line printed mid-flush waits in the ring")
    h.ok(m.ring() > 0, "and is queued")
    tick(m, 2100)
    h.has("printed-during-flush", "then goes out with the next flush", m=m)
    h.clean(m)


# ---------------------------------------------------------------- seam: stack
def t_stack(h):
    h.section = "seam stack depth"
    print("\nseam: deepest stack from the 0x22360 stub through v5_tick")
    results = {}
    at_call = {}  # deepest payload depth at each modelled stock call, over all cases

    def ejected(m):  # host ejected a drive session: the tick remounts, then hands back
        m.poke8(m.sym["st"] + ST_ACTIVE, 1)
        m.poke8(m.sym["st"] + ST_RELEASED, 1)
        m.poke16(H_LUN0, 0xFFAA)
        m.sd_up = m.mounted = False

    cases = (
        ("long set line", "set " + "x" * 120 + "\n", None),
        ("set + led", "set display=off\nled off\n", None),
        ("reboot", "reboot\n", None),
        ("remount after eject + set", "set display=off\n", ejected),
        ("webcam 5", "webcam 5\n", None),
    )
    for label, cmd, setup in cases:
        m = boot(cmd=cmd)
        allow_reboot(m)
        if setup:
            setup(m)
        sp0 = STACK - 0x800
        low = [sp0]
        where = [0]

        def track(uc, a, size, _, low=low, where=where):
            s = uc.reg_read(UC_ARM_REG_SP)
            if s < low[0]:
                low[0] = s
                where[0] = a

        hk = m.uc.hook_add(UC_HOOK_CODE, track)
        m.stack_low = sp0
        m.ticks += 5000
        m.task("periph")
        m.call_arm(0x22360, sp=sp0, until=0x22364, limit=20_000_000)
        m.uc.hook_del(hk)
        depth = max(sp0 - low[0], sp0 - m.stack_low)
        results[label] = (depth, where[0])
        for name, csp in m.call_sp.items():
            at_call[name] = max(at_call.get(name, 0), sp0 - csp)
    worst = max(d for d, _ in results.values())
    for label, (depth, pc) in results.items():
        print(f"  {label}: {depth} B below the 0x22360 call, deepest sp at pc {pc:#x}")
    room = TASK_STACK - TASK_FRAME - IRQ_FRAME
    print(f"  payload depth at each modelled stock call (the stock code's own use must fit in {room} B minus this):")
    for name, d in sorted(at_call.items(), key=lambda kv: -kv[1]):
        print(f"    {name:12s} {d:4d} B")
    total = TASK_FRAME + worst + IRQ_FRAME
    print(
        f"  task frame {TASK_FRAME} + deepest {worst} + IRQ save {IRQ_FRAME} = {total} B "
        f"of {TASK_STACK} (stock file, heap and queue calls are modelled, their own frames "
        f"are not in this number)"
    )
    h.ok(total <= TASK_STACK, f"fits the {TASK_STACK} B peripheral task stack ({total} B)")


# ---------------------------------------------------------------- webcam gates
def t_v6(h):
    h.section = "webcam gates"
    print("\nwebcam gates: left/right and record start are ignored while the host streams")
    vs_alt, idx, sp = 0x123D3C, 0x1217BD, STACK - 0x200
    for alt, passes in ((0, True), (3, False)):
        m = boot()
        m.poke32(vs_alt, alt)
        what = f"alt {alt}"
        for site in (0x2EC48, 0x37104, 0x37134):
            m.poke8(idx, 1)
            m.call_arm(site, 1, sp=sp, until=site + 4)
            h.eq(m.u8(idx) != 1, passes, f"{what}: {site:#x} {'steps' if passes else 'leaves'} the aspect index")
            h.eq(m.reg(UC_ARM_REG_SP), sp, f"{what}: {site:#x} sp intact")
        # 0x35890 with no record flags: ands sets Z, so the beq at 0x35898 is taken
        stop = 0x35A10 if passes else 0x3589C
        try:
            m.call_arm(0x35890, sp=sp, regs={UC_ARM_REG_R3: 0, UC_ARM_REG_R4: 7}, until=stop, limit=50)
            got = stop
        except EmuError as e:
            got = str(e)
        h.eq(got, stop, f"{what}: record start goes to {stop:#x}")
        h.eq(m.u8(B_REC_FLAGS), 0, f"{what}: record flags untouched")
        h.clean(m)


# ---------------------------------------------------------------- webcam frame sizes
V6_FRAMES = [(1920, 1080), (1280, 720), (640, 360), (320, 180), (256, 144), (128, 72)]
V6_CFG_STOCK = 0xE39D0  # stock's config descriptor; nothing points at it in the patched image
PROBE, PROBE_HOST, UVC_ERR = 0xE3B2C, 0x124428, 0x12440A  # committed probe, host's data, request error
# register_desc table, +4 = config; EP0 state +0x28 data, +0x2c length, +0x36 wLength, +0x38 zero-length flag
UVC_DESC = 0x134E34
ZOOM_A, ZOOM_B = 0xF19F4, 0xF19C0  # per-path zoom bytes, paths 0 and 1 (0x12d68)
SENSOR_TBL, SENSOR_OBJ = 0x124164, 0x00A05000  # [0] = sensor struct, W/H at +0x34/+0x36 (0x4bb94, 0x4bbd4)


def syms():
    S = {}
    for line in open(SYM):
        a, _, name = line.split()
        S[name] = int(a, 16)
    return S


def descs(b):
    """(offset, length, type, subtype, interface (number, alt, class, subclass)) per descriptor,
    up to a length that does not fit"""
    out, o, itf = [], 0, None
    while o + 2 <= len(b):
        n = b[o]
        if n < 3 or o + n > len(b):
            break
        if b[o + 1] == 4:
            itf = (b[o + 2], b[o + 3], b[o + 5], b[o + 6])
        out.append((o, n, b[o + 1], b[o + 2], itf))
        o += n
    return out


def t_v6_desc(h):
    h.section = "webcam: config descriptor"
    print("\nwebcam config descriptor v6_cfg: walk, lengths, frames, the two words that point at it")
    d, base, S = open(IMAGE, "rb").read(), open(BASE, "rb").read(), syms()
    c0 = S["v6_cfg"]

    def w32(a):
        return struct.unpack_from("<I", d, a + B)[0]

    h.eq(w32(0x3EF00), c0, "pool 0x3ef00 (register_desc(1) in usb_uvc_init) = v6_cfg")
    h.ok(c0 % 4 == 0 and GAP <= c0, f"v6_cfg {c0:#x} is 4-aligned (0xbc9cc reads it with word loads), in the gap")
    tot = struct.unpack_from("<H", d, c0 + B + 2)[0]
    cfg = d[c0 + B : c0 + B + tot]
    h.ok(c0 + tot <= S["__gap_end"] <= GAP_END, f"wTotalLength {tot:#x}: v6_cfg ends inside the copied gap")
    n0 = struct.unpack_from("<H", base, V6_CFG_STOCK + B + 2)[0]
    old = base[V6_CFG_STOCK + B : V6_CFG_STOCK + B + n0]
    h.eq(d[V6_CFG_STOCK + B : V6_CFG_STOCK + B + n0], old, "stock's config at 0xe39d0 left as stock")
    ds, ods = descs(cfg), descs(old)
    h.eq(sum(n for _, n, *_ in ds), tot, "descriptor lengths add up to wTotalLength")
    h.eq((tot, len(ds)), (0x200, len(ods) + 5), f"512 B, {len(ods) + 5} descriptors: stock's {len(ods)} + 5 frames")
    h.eq(cfg[4], len({i[0] for *_, i in ds if i}), "bNumInterfaces = the interfaces in it (4)")

    vs = [x for x in ds if x[2] == 0x24 and x[4] and x[4][2:] == (0x0E, 2)]  # video streaming, class-specific
    hdr = [x for x in vs if x[3] == 1]
    fmt = [x for x in vs if x[3] == 6]
    frm = [x for x in vs if x[3] == 7]
    h.eq((len(hdr), len(fmt), len(frm)), (1, 1, 6), "one VS input header, one MJPEG format, six frames")
    if not (hdr and fmt and frm):
        return
    ho, fo = hdr[0][0], fmt[0][0]
    h.eq(
        struct.unpack_from("<H", cfg, ho + 4)[0],
        sum(x[1] for x in vs),
        "VS header wTotalLength = header + format + frames",
    )
    h.eq(struct.unpack_from("<H", cfg, ho + 4)[0], 0xFD, "VS header wTotalLength 0xfd (stock 0x3f + 5 x 0x26)")
    h.eq((cfg[fo + 3], cfg[fo + 4], cfg[fo + 6]), (1, 6, 1), "format 1: bNumFrameDescriptors 6, default frame 1")
    got = []
    for o, n, *_ in frm:
        f = cfg[o : o + n]
        got.append((f[3], *struct.unpack_from("<HH", f, 5), struct.unpack_from("<I", f, 17)[0]))
    want = [(i + 1, w, hh, w * hh * 2) for i, (w, hh) in enumerate(V6_FRAMES)]
    h.eq(got, want, "frames 1..6: 1920x1080 1280x720 640x360 320x180 256x144 128x72, buffer W*H*2")
    f1 = cfg[frm[0][0] : frm[0][0] + 0x26]
    same = all(cfg[o + 9 : o + 17] == f1[9:17] and cfg[o + 21 : o + 0x26] == f1[21:] for o, *_ in frm)
    h.ok(same, "every frame: stock's bit rates and 30 fps (333333, continuous, min = max)")
    oldf = [x for x in ods if x[2] == 0x24 and x[3] == 7][0][0]
    h.eq(
        (f1[:5], f1[9:17], f1[21:]),
        (old[oldf : oldf + 5], old[oldf + 9 : oldf + 17], old[oldf + 21 : oldf + 0x26]),
        "frame 1 is stock's frame 1 apart from W, H and buffer size",
    )

    def strip(b, walk):
        """the descriptors minus the frames, with the three length fields zeroed"""
        out = []
        for o, n, t, s, itf in walk:
            x = bytearray(b[o : o + n])
            if t == 0x24 and s == 7 and itf and itf[2:] == (0x0E, 2):
                continue
            if t == 2:
                x[2:4] = b"\0\0"
            if t == 0x24 and s == 1 and itf and itf[2:] == (0x0E, 2):
                x[4:6] = b"\0\0"
            if t == 0x24 and s == 6 and itf and itf[2:] == (0x0E, 2):
                x[4] = 0
            out.append(bytes(x))
        return out

    h.eq(strip(cfg, ds), strip(old, ods), "everything else is stock's, in stock's order")
    ep = [o for o, n, t, *_ in ds if t == 5 and cfg[o + 2] == 0x85]
    oep = [o for o, n, t, *_ in ods if t == 5 and old[o + 2] == 0x85]
    h.eq(oep[-1] + 4, 0xD4, "stock: the alt-3 EP 0x85 wMaxPacketSize is at cfg+0xd4 (0x52370 writes there)")
    h.eq(w32(0x52488) + 0xD4, c0 + ep[-1] + 4, "pool 0x52488 + 0xd4 = v6_cfg's alt-3 wMaxPacketSize")

    print("  the stock USB code on v6_cfg")
    m = Machine(IMAGE, SYM)
    m.call_arm(0x3EDC4, until=0x3EDD0)  # mov r0,#1; ldr r1,=[0x3ef00]; bl register_desc
    h.eq(m.u32(UVC_DESC + 4), c0, "register_desc(1) at 0x3edc4 stores v6_cfg")
    for typ in (2, 7):
        m.call_arm(0x508B4, until=0x50860, regs={UC_ARM_REG_R4: UVC_DESC, UC_ARM_REG_R12: typ})
        h.eq(
            (m.u32(UVC_DESC + 0x28), m.u32(UVC_DESC + 0x2C), m.u8(c0 + 1)),
            (c0, 0x200, typ),
            f"GET_DESCRIPTOR type {typ} (0x508b4): v6_cfg, 512 B, byte 1 = {typ}",
        )
    for wlen, sent, zlp in ((9, 9, 0), (0xFF, 0xFF, 0), (0x200, 0x200, 0), (0x400, 0x200, 1)):
        m.poke32(UVC_DESC + 0x2C, 0x200)
        m.poke16(UVC_DESC + 0x36, wlen)
        m.poke8(UVC_DESC + 0x38, 0)
        m.call_arm(0x4D2E0)
        h.eq(
            (m.u32(UVC_DESC + 0x2C), m.u8(UVC_DESC + 0x38)),
            (sent, zlp),
            f"EP0 clamp 0x4d2e0, wLength {wlen:#x}: sends {sent:#x} B, zero-length packet flag {zlp}",
        )
    gw = m.watch_writes(GAP, GAP_END)
    sw = m.watch_writes(V6_CFG_STOCK, V6_CFG_STOCK + n0)
    m.call_arm(0x52364, until=0x52378)  # L2 event 10
    h.eq(
        [(a - c0, s, v) for a, s, v, _ in gw],
        [(ep[-1] + 4, 1, 0), (ep[-1] + 5, 1, 4)],
        "L2 event 10 writes 0x0400 to v6_cfg's alt-3 wMaxPacketSize, nothing else in the gap",
    )
    h.eq(sw, [], "and nothing at 0xe39d0")
    h.clean(m)


def t_v6_probe(h):
    h.section = "webcam: probe"
    print("\nprobe/commit SET_CUR 0x52644 per bFrameIndex: 0..6 pass, 7 fails with error 6")
    m = Machine(IMAGE, SYM)
    cases = [(i, 1, 333333, 0 if i <= 6 else 6) for i in range(8)]
    cases += [(2, 2, 333333, 6), (2, 1, 666666, 6)]  # format 2, 15 fps: still refused
    for idx, fmt, ival, err in cases:
        m.put(PROBE_HOST, struct.pack("<HBBI", 1, fmt, idx, ival) + bytes(18))
        m.poke8(UVC_ERR, 0xAA)
        m.call_arm(0x52644, until=0x52530)
        st = bytes(m.uc.mem_read(PROBE, 26))
        h.eq(
            (m.u8(UVC_ERR), st[3], struct.unpack_from("<II", st, 18)),
            (err, idx, (0x1C2000, 0x400)),
            f"format {fmt} frame {idx} interval {ival}: error {err}, index stored, "
            "dwMaxVideoFrameSize 0x1c2000 and dwMaxPayloadTransferSize 0x400 as stock",
        )


def t_v6_size(h):
    h.section = "webcam: frame size"
    print("\nsensor_on(0x106)'s geometry 0xf004 per committed bFrameIndex, sensor 1920x1080")
    m = Machine(IMAGE, SYM)
    m.poke32(SENSOR_TBL, SENSOR_OBJ)
    m.poke16(SENSOR_OBJ + 0x34, 1920)
    m.poke16(SENSOR_OBJ + 0x36, 1080)
    for idx in (0, 1, 2, 3, 4, 5, 6, 7, 0xFF):
        w, hh = V6_FRAMES[idx - 1] if 1 <= idx <= 6 else V6_FRAMES[0]
        g = m.alloc(0x40)
        m.put(g, bytes(0x40))
        m.poke16(ZOOM_A, 0)  # as after a boot into Metering: never set
        m.poke16(ZOOM_B, 0)
        m.poke8(PROBE + 3, idx)
        m.reset()
        r = m.call_arm(0xF004, g, 0x106, sp=STACK - 0x200)

        def u16(o, g=g):
            return m.u16(g + o)

        out = "".join(chr(t[1]) for t in m.calls("uart"))
        h.eq(
            (
                r,
                (u16(6), u16(8)),
                (u16(0xE), u16(0x10)),
                (u16(0x1A), u16(0x1C), u16(0x1E), u16(0x20)),
                (u16(0x32), u16(0x34)),
                (m.u8(g + 0x30), m.u8(g + 0x31)),
            ),
            (0, (1920, 1080), (1920, 1080), (w, hh, w, hh), (w, hh), (9, 0)),
            f"index {idx}: path 0 and target 1920x1080, second path and display {w}x{hh}",
        )
        h.eq(
            [m.u8(a) for a in (ZOOM_A, ZOOM_A + 1, ZOOM_B, ZOOM_B + 1)],
            [100] * 4,
            f"index {idx}: zoom 100 on paths 0, 1",
        )
        h.ok(
            f"[T]Dw:{w},Dh:{hh}," in out and "[Fmode]3,[Rmode]1)" in out and "Tw:1920,Th:1080" in out,
            f"index {idx}: prints [T]Dw:{w},Dh:{hh}, Tw:1920,Th:1080 and [Fmode]3,[Rmode]1)",
            f"({out!r})",
        )
    print("  v6_size itself: only r0, r3 and ip change")
    regs = {r: 0x5000 + i for i, r in enumerate((UC_ARM_REG_R1, UC_ARM_REG_R2) + CALLEE_SAVED)}
    m.poke8(PROBE + 3, 4)
    m.call_arm(m.sym["v6_size"], sp=STACK - 0x100, regs=regs)
    h.eq((m.reg(UC_ARM_REG_R0), m.reg(UC_ARM_REG_R3)), (320, 180), "index 4: r0 = 320, r3 = 180")
    h.ok(all(m.reg(r) == v for r, v in regs.items()), "r1, r2, r4-r11 intact")
    h.eq(m.reg(UC_ARM_REG_SP), STACK - 0x100, "sp intact")
    h.clean(m)


# ---------------------------------------------------------------- CMD.TXT webcam
ONCE = "WEBCAM.ONE"


def settle(m):
    """after act_reboot came back with no power cut: the tick remounts the card, then flushes"""
    for ms in (6000, 2100, 2100):
        tick(m, ms)


def opened(m, i0=0):
    """(trace index, file name, flags) of each open from trace index i0"""
    return [(i, m.cstr(t[1]), t[2]) for i, t in enumerate(m.trace) if i >= i0 and t[0] == "open"]


def t_webcam_cmd(h):
    h.section = "webcam command"
    print("\nCMD.TXT webcam [MIN]: WEBCAM.ONE written and flushed, then the reboot path")
    for arg, want in (
        ("", b"0\r\n"),
        (" 0", b"0\r\n"),
        (" 5", b"5\r\n"),
        (" 1440", b"1440\r\n"),
        (" 05", b"5\r\n"),
        ("\t7\r", b"7\r\n"),
    ):
        m = boot(cmd=f"webcam{arg}\n")
        allow_reboot(m)
        tick(m, 5000)
        what = f"`webcam{arg.rstrip()}`"
        h.eq(m.card.get(ONCE), want, f"{what}: WEBCAM.ONE holds {want!r}")
        h.eq([t[2] for t in m.calls("apq")], [0x7007], f"{what}: one post, 0x7007")
        tr, n = m.trace, m.names()
        mk = [i for i, name, fl in opened(m) if name == "G:\\" + ONCE and fl == 0x601]
        iu = n.index("unmount") if "unmount" in n else -1
        ia = n.index("apq") if "apq" in n else -1
        ok = (
            len(mk) == 1
            and n[mk[0] + 1 : mk[0] + 3] == ["write", "close"]
            and "flush" in n[mk[0] : iu]
            and n.index("flush", mk[0]) < iu < ia
        )
        h.ok(ok, f"{what}: marker created, written, closed and flushed before the unmount and the post", f"({n})")
        h.ok(
            not any(t[0] in ("open", "write", "unlink") for t in tr[iu:]),
            f"{what}: no file call after the unmount: the marker stays for the next boot",
        )
        if arg == " 5":
            tick(m, 2100)
            h.has("[v5] webcam 5\r\n[v5] reboot\r\n", m=m)
        h.clean(m)

    print("  bad arguments: nothing written, nothing posted")
    for arg in ("abc", "-1", "1441", "99999999999", "5x", "+5", "5 6", "0x5"):
        m = boot(cmd=f"webcam {arg}\n")
        allow_reboot(m)
        tick(m, 5000)
        tick(m, 2100)
        h.ok(
            ONCE not in m.card.names() and not m.calls("apq") and "unmount" not in m.names(),
            f"`webcam {arg}`: no marker, no unmount, no post",
            f"({m.card.names()}, {m.names()})",
        )
        h.has("cmd: bad webcam", f"`webcam {arg}`: LOG.TXT `cmd: bad webcam`", m=m)

    print("  refused like reboot: nothing written")
    for name, addr, val, wide in (
        ("capture busy", B_CAPTURE_BUSY, 1, False),
        ("recording", B_REC_FLAGS, 2, False),
        ("record transition", B_REC_FLAGS, 8, False),
        ("startup state", W_APP_STATE, 0x100, True),
    ):
        m = boot(cmd="webcam 5\n")
        allow_reboot(m)
        (m.poke16 if wide else m.poke8)(addr, val)
        tick(m, 5000)
        tick(m, 2100)
        h.ok(
            ONCE not in m.card.names() and not m.calls("apq") and "unmount" not in m.names(),
            f"{name}: no marker, no unmount, no post",
            f"({m.card.names()}, {m.names()})",
        )
        h.has("[v5] reboot 3", f"{name}: LOG.TXT `reboot 3`", m=m)

    print("  the unmount fails: no post, the marker is deleted again")
    m = boot(cmd="webcam 5\n")
    allow_reboot(m)
    m.unmount_ret = -1
    tick(m, 5000)
    tick(m, 2100)
    h.eq((m.calls("apq"), ONCE in m.card.names()), ([], False), "no post, no WEBCAM.ONE left")
    h.has("[v5] reboot ffffffff\r\n[v5] webcam: marker removed", m=m)
    h.eq(leaks(m), {}, "no heap left behind")
    h.clean(m)


def t_webcam_boot(h):
    h.section = "webcam boot"
    print("\nboot with WEBCAM.ONE: deleted first, then the webcam on every switch position")
    for mode in (0, 1, 2):
        m = boot(files={ONCE: "5\r\n"}, mode=mode)
        m.gpio_input(95, 0)
        plug(m)
        h.eq([t[2] for t in m.calls("qsend")], [0x8003], f"switch {mode}: plug-in posts the webcam init 0x8003")
        h.ok(ONCE not in m.card.names(), f"switch {mode}: WEBCAM.ONE deleted")
        tick(m, 2100)
        h.has("[v5] webcam: boot 5\r\n", f"switch {mode}: LOG.TXT `webcam: boot 5`", m=m)
        h.has(f"[v5] usb {mode} webcam", m=m)
        h.eq(leaks(m), {}, f"switch {mode}: no heap left behind")
    m = boot(mode=0)
    m.gpio_input(95, 0)
    plug(m)
    h.eq([t[2] for t in m.calls("qsend")], [0x8001], "no marker, Photo: the drive, as before")

    print("  the delete and its flush come before anything is acted on")
    m = Machine(IMAGE, SYM, Card({"C100.TXT": CFG_DEFAULT, ONCE: "5\r\n"}))
    acts = []
    for name in ("webcam_forced", "reboot_at"):
        a = m.sym[name]

        def wr(uc, access, addr, size, value, _, name=name):
            if value:
                acts.append((len(m.trace), name))

        m.uc.hook_add(UC_HOOK_MEM_WRITE, wr, begin=a, end=a + (3 if name == "reboot_at" else 0))
    mount(m)
    n = m.names()
    iu = [i for i, t in enumerate(m.trace) if t[0] == "unlink" and m.cstr(t[1]) == "G:\\" + ONCE]
    ok = len(iu) == 1 and "flush" in n[iu[0] :] and acts and all(i > n.index("flush", iu[0]) for i, _ in acts)
    h.ok(ok, "unlink, flush, then webcam_forced and the timer are set", f"(unlink {iu}, acts {acts}, {n})")
    h.eq(sorted({x for _, x in acts}), ["reboot_at", "webcam_forced"], "both set")

    print("  webcam 5: the reboot 5 minutes after the boot, not before")
    m = boot(files={ONCE: "5\r\n"})
    allow_reboot(m)
    t0 = m.ticks  # load_settings ran at this tick
    tick(m, 0)
    m.ticks = t0 + 5 * 60000 - 2
    tick(m)
    h.eq(m.calls("apq"), [], "4:59.998 after the boot: nothing posted")
    m.ticks = t0 + 5 * 60000 + 1
    m.reset()
    tick(m)
    h.eq([t[2] for t in m.calls("apq")], [0x7007], "5:00 after the boot: 0x7007 posted")
    h.called("unmount", "through act_reboot: the card unmounted first", m=m)
    h.ok(ONCE not in m.card.names(), "no WEBCAM.ONE on the card: the next boot follows the switch")
    settle(m)
    h.has("[v5] webcam: time up\r\n[v5] reboot\r\n", m=m)
    m2 = Machine(IMAGE, SYM, m.card)
    mount(m2)
    m2.poke8(B_USB_POLL_EN, 1)
    m2.poke16(W_APP_STATE, 0x102)
    m2.poke8(B_MODE, 0)
    m2.gpio_input(95, 0)
    plug(m2)
    h.eq([t[2] for t in m2.calls("qsend")], [0x8001], "next boot on that card, Photo: the drive")
    tick(m2, 10 * 60000)
    h.eq(m2.calls("apq"), [], "and no timer")

    print("  a refused reboot is tried again a minute later")
    m = boot(files={ONCE: "1\r\n"})
    allow_reboot(m)
    m.poke8(B_CAPTURE_BUSY, 1)
    tick(m, 60000 + 10)
    h.eq(m.calls("apq"), [], "capturing at 1:00: nothing posted")
    tick(m, 59000)
    h.eq(len([t for t in m.trace if t[0] == "apq"]), 0, "nothing in the next 59 s")
    m.poke8(B_CAPTURE_BUSY, 0)
    tick(m, 1002)
    h.eq([t[2] for t in m.calls("apq")], [0x7007], "60 s later, not busy: posted")
    settle(m)
    h.has("[v5] webcam: time up\r\n[v5] reboot 3\r\n", m=m)

    print("  webcam (no minutes): the webcam until switched off, no timer")
    m = boot(files={ONCE: "0\r\n"}, mode=0)
    allow_reboot(m)
    m.gpio_input(95, 0)
    plug(m)
    h.eq([t[2] for t in m.calls("qsend")], [0x8003], "Photo: the webcam")
    tick(m, 2 * 24 * 3600 * 1000)
    h.eq(m.calls("apq"), [], "two days later: nothing posted")
    h.has("[v5] webcam: boot 0\r\n", m=m)

    print("  the marker cannot be deleted: a normal boot, the marker stays")
    m = Machine(IMAGE, SYM, Card({"C100.TXT": CFG_DEFAULT, ONCE: "5\r\n"}))
    m.unlink_ret = -1
    mount(m)
    m.poke8(B_USB_POLL_EN, 1)
    m.poke16(W_APP_STATE, 0x102)
    m.gpio_input(95, 0)
    tick(m, 2100)  # the log goes out before the drive takes the card
    h.has("[v5] webcam: rm fail", m=m)
    h.ok("webcam: boot" not in log(m), "no `webcam: boot`")
    plug(m)
    h.eq([t[2] for t in m.calls("qsend")], [0x8001], "Photo: the drive")
    h.eq(m.card.get(ONCE), b"5\r\n", "WEBCAM.ONE still there")
    tick(m, 10 * 60000)
    h.eq(m.calls("apq"), [], "no timer")

    print("  a bad or unreadable marker is deleted and ignored")
    for text, line in (
        ("abc\r\n", "webcam: bad abc"),
        ("1441\r\n", "webcam: bad 1441"),
        ("-5", "webcam: bad -5"),
        ("5 6\r\n", "webcam: bad 5 6"),
        ("", "webcam: bad \r\n"),
        ("1" * 20, "webcam: bad file"),
    ):
        m = boot(files={ONCE: text}, mode=0)
        allow_reboot(m)
        m.gpio_input(95, 0)
        tick(m, 2100)
        h.has(line, m=m)
        plug(m)
        tick(m, 10 * 60000)
        h.ok(
            ONCE not in m.card.names() and [t[2] for t in m.calls("qsend")] == [0x8001] and not m.calls("apq"),
            f"{text[:8]!r}: deleted, Photo is the drive, no timer",
            f"({m.card.names()}, {m.calls('qsend')}, {m.calls('apq')})",
        )
        h.eq(leaks(m), {}, f"{text[:8]!r}: no heap left behind")

    print("  once per boot, after the update check")
    m = boot(files={ONCE: "0\r\n"})
    m.card.f[ONCE] = bytearray(b"5\r\n")  # put back over USB, then the card re-inserted
    mount(m)
    h.eq(m.card.get(ONCE), b"5\r\n", "a second mount in the same boot leaves a new marker for the next boot")
    m = Machine(IMAGE, SYM, Card({"C100.TXT": CFG_DEFAULT, ONCE: "5\r\n"}))
    m.poke8(B_UPD_CHECKED, 2)  # the updater started on this boot
    mount(m)
    h.eq((m.card.get(ONCE), m.hits["load_settings"]), (b"5\r\n", 0), "a mount during an update: marker untouched")
    h.clean(m)


TESTS = (
    t_image,
    t_heap,
    t_loader,
    t_dispatch,
    t_disarmed,
    t_settings,
    t_settings_off,
    t_bad_keys,
    t_log_capture,
    t_log_bounds,
    t_cmd,
    t_reboot,
    t_sound,
    t_hook_sound,
    t_led_gate,
    t_get95,
    t_long_select,
    t_hook_k1,
    t_usb_plugin,
    t_usb_busy_defers,
    t_eject_cb,
    t_hook_eject,
    t_remount,
    t_no_card,
    t_empty_card,
    t_hook_chain,
    t_reentry,
    t_stack,
    t_v6,
    t_v6_desc,
    t_v6_probe,
    t_v6_size,
    t_webcam_cmd,
    t_webcam_boot,
)


def main():
    global IMAGE, SYM, BASE
    IMAGE, SYM = sys.argv[1], sys.argv[2]
    BASE = sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, "..", "obj", "base.bin")
    h = Harness(None)
    probe = Machine(IMAGE, SYM)
    g, o0, o1 = probe.sym["__gap_end"], probe.sym["__ovl_start"], probe.sym["__ovl_end"]
    print(f"gap end {g:#x}, gap left {GAP_END - g} B; overlay {o0:#x}..{o1:#x}, {o1 - o0} B")
    only = os.environ.get("ONLY")
    for t in TESTS:
        if only and only not in t.__name__:
            continue
        try:
            t(h)
        except (EmuError, UcError, AssertionError, KeyError, ValueError) as e:
            h.ok(False, f"{t.__name__} crashed", f"({type(e).__name__}: {e})")
    return h.report()


if __name__ == "__main__":
    sys.exit(main())
