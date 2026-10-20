# Payload tests under emulation. usage, from the repo root after ./build.sh:
#   uv run --group test python tests/test_v5.py build/GP_CARDVR_UPGRADE.BIN obj/v5.sym [BASE]
# BASE defaults to obj/base.bin, the image tools/patch.py patched. ONLY=name runs only the
# tests whose function name contains it.
# Not with python -I: it drops the script directory and `import emu` fails.
import os
import struct
import sys

from emu import (
    A_EJECT,
    A_GPIO_SET,
    A_LUN_REG,
    A_PRINT_BUF,
    A_PRINTF,
    A_START_STOP,
    APQ_OBJ,
    B_CAPTURE_BUSY,
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
    RET,
    SD_OPS,
    STACK,
    USBQ_H,
    W_APP_STATE,
    B,
    Card,
    EmuError,
    Harness,
    Machine,
)
from unicorn import UC_HOOK_CODE, UcError
from unicorn.arm_const import (
    UC_ARM_REG_R0,
    UC_ARM_REG_R3,
    UC_ARM_REG_R4,
    UC_ARM_REG_R5,
    UC_ARM_REG_R6,
    UC_ARM_REG_R7,
    UC_ARM_REG_R8,
    UC_ARM_REG_R9,
    UC_ARM_REG_R10,
    UC_ARM_REG_SP,
)

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_DEFAULT = "display=on\nled=on\nsound=on\nlog=on\nusb_photo=drive\nusb_video=webcam\n"

# struct offsets, v5.h
CFG_DISPLAY, CFG_LED, CFG_SOUND, CFG_LOG, CFG_USB = 0, 1, 2, 3, 4
ST_ACTIVE, ST_PENDING, ST_RELEASED, ST_STALE, ST_TOGGLE, ST_SOUND, ST_BOOTED, ST_EJECTING = range(8)
LED_OFF, LED_WANT43, LED_WANT95 = 0, 1, 2

# Peripheral task: 4096 B stack (InitTask def at 0xda8ec, stack 0x800 in stock, 0x1000 with
# the patch, docs/hooks.md). The task function 0x21d38 pushes 9 registers (0x21d3c) and
# drops sp by 0x2c (0x21d58): 80 B under its entry sp when the 0x6001 case reaches 0x22238.
# An IRQ taken in the task saves 18 words on the task stack (lr, r0-r14, spsr, critical
# nesting: 0x5c6c8..0x5c704).
TASK_STACK, TASK_FRAME, IRQ_FRAME = 4096, 80, 72

# The hook map, as tools/patch.py applies it: (id, RAM, original, kind, target)
HOOKMAP = [
    ("LOG", 0x535E0, "0000d4e5", "b", "logcap_stub"),
    ("SET", 0x37DF4, "58d08de2", "b", "settings_stub"),
    ("D1", 0x22238, "04fcffeb", "bl", "tick_stub"),
    ("G1", 0x1F540, "5f00a0e3", "b", "plug_stub"),
    ("G2", 0x1F4C0, "667a02eb", "bl", "unplug_stub"),
    ("G3a", 0x22138, "8500000a", "bal", "usb_poll_gate_stub"),
    ("G3b", 0x22354, "02f4ffeb", "bl", "usb_tick_stub"),
    ("G4", 0x36664 - B, "05800000", "bytes", "02800000"),
    ("G6", 0xE0848, "30e20b00", "thumbword", "v5_eject_cb"),
    ("G7", 0x32158 - B, "01", "bytes", "00"),
    ("G7", 0x3215F - B, "03", "bytes", "13"),
    ("G7", 0x32163 - B, "05", "bytes", "15"),
    ("G7", 0x32167 - B, "0a", "bytes", "1a"),
    ("K1", 0x1DDA4, "0300008a", "b", "long_select_stub"),
    ("L1", 0x43224, "0036a0e1", "b", "v5_gpio_set"),
    ("L2", 0x1F544, "278f00eb", "bl", "v5_get95"),
    ("L3", 0x1F5EC, "fd8e00eb", "bl", "v5_get95"),
    ("S1", 0x22958, "f0402de9", "b", "v5_sound_gate"),
    ("STACK", 0xEAAFC - B, "00080000", "bytes", "00100000"),
    ("V6A", 0x2EA90, "20caffeb", "bl", "v6_aspect_gate"),
    ("V6A", 0x36F4C, "f1a8ffeb", "bl", "v6_aspect_gate"),
    ("V6A", 0x36F7C, "e5a8ffeb", "bl", "v6_aspect_gate"),
    ("V6R", 0x356E0, "5c00000a", "b", "v6_rec_gate"),
    ("V6D", 0xF3B0A - B, "0005d002", "bytes", "80073804"),
    ("V6D", 0xF3B16 - B, "00201c00", "bytes", "00483f00"),
    ("V6D", 0x1F2E4 - B, "2d2ea0e3", "bytes", "a8279fe5"),
    ("V6D", 0x1F2F0 - B, "053ca0e3", "bytes", "783ea0e3"),
]
LOADLEN = 0x1002E
SIZE, TEST_PAD = 1841152, 0x200000  # tools/patch.py: a test-only build is padded with 0xFF
B_UPD_CHECKED = 0x123CFC  # 0 at boot, 2 = updater started, 1 = first mount found no update
W_MSDC_CB = 0x124254  # SET_CONFIG callback usb_msdc_init leaves behind (fw.h)
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
    # what the photo/video mount-OK handler does next (0x2ec00, 0x362e0)
    m.poke8(B_USB_POLL_EN, 1)
    m.poke32(W_APP_STATE, 0x102)
    m.poke8(B_MODE, mode)
    m.boot_trace, m.boot_violations = m.trace, m.violations
    m.reset()
    return m


def mount(m, r4=0xC0DE0004):
    """The stock mount path reaching the SET hook in the storage task. The VERSION.TXT
    writer 0x37d88 holds 0x58 B of locals over its pushed r4 and lr there."""
    m.task("storage")
    sp = STACK - 0x100
    m.poke32(sp + 0x58, r4)
    m.poke32(sp + 0x5C, RET)
    m.call_arm(0x37DF4, sp=sp)
    m.poke8(B_UPD_CHECKED, 1)  # 0x3e3b0, once the mount returns without an update
    m.task("periph")
    return sp


def tick(m, ms=0):
    """One 0x6001 pass of the peripheral task, entered at the D1 site."""
    m.ticks += ms
    m.task("periph")
    m.call_arm(0x22238, sp=STACK - 0x100, until=0x2223C)


def stv(m, off):
    return m.u8(m.sym["st"] + off)


def cfgv(m, off):
    return m.u8(m.sym["cfg"] + off)


def log(m):
    return m.card.get("LOG.TXT").decode("latin1")


def allow_reboot(m):
    m.poke8(B_CAPTURE_BUSY, 0)
    m.poke8(B_REC_FLAGS, 0)
    m.poke32(W_APP_STATE, 0x102)


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
        elif kind == "thumbword":
            h.eq(struct.unpack("<I", got)[0], S[val] | 1, f"{hid} {at:#x}: word = {val} | 1")
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
    end = S["__v5_end"]
    touched.update(range(GAP + B, end + B))
    stray = [o for o in range(SIZE) if d[o] != base[o] and o not in touched]
    h.ok(
        not stray,
        "no byte differs from the base outside the hook map, load length and payload",
        f"({len(stray)} B, first at file {stray[0]:#x})" if stray else "",
    )
    name = os.path.basename(IMAGE).upper()
    if end > GAP_END:
        h.ok(name != "GP_CARDVR_UPGRADE.BIN", "test-only build is not named GP_CARDVR_UPGRADE.BIN")
        h.ok(
            len(d) == TEST_PAD and set(d[SIZE:]) == {0xFF},
            "test-only build padded with 0xFF to 2 MiB, past the updater's 1,846,000 B gate",
            f"({len(d)} B)",
        )
        # a build with GAP_END raised puts .data/.bss in stock .bss, which crt0 zeroes
        print(
            f"  WARN payload ends at {end:#x}, past stock .bss start {GAP_END:#x}: cfg's "
            f"initializer, the log ring pointer and size live at {S['cfg']:#x}.."
            f"{end:#x} and are zeroed at boot on the camera (the emulator does not run crt0)"
        )
    else:
        h.eq(len(d), SIZE, "flashable build is the stock size")
        h.ok(struct.unpack_from("<I", d, LOADLEN)[0] <= 0x741, "load length within 0x741")


def t_dispatch(h):
    h.section = "seam D1 dispatch"
    print("\nseam D1 through the stock dispatch: one 0x6001 message, back to the receive")
    # after the queue receive (bl 0x51c at 0x21d94, so lr = 0x21d98) the task loop
    # dispatches [sp+0x24] through the table at 0x21dac; 0x6001 is entry 1 = 0x22238
    m = boot()
    sp = STACK - 0x400
    m.poke32(sp + 0x24, 0x6001)
    loop = {
        UC_ARM_REG_R4: 0x131F8C,
        UC_ARM_REG_R6: 0x1216F8,
        UC_ARM_REG_R7: 0x1329DC,
        UC_ARM_REG_R8: 0x1216F4,
        UC_ARM_REG_R9: 0x1216E4,
        UC_ARM_REG_R10: 0x131FA8,
    }
    m.reset()
    try:
        m.call_arm(0x21DA0, 0, sp=sp, lr=0x21D98, regs=loop, until=0x51C, limit=2_000_000)
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
    m = Machine(IMAGE, SYM, Card({"C100.TXT": "led=off\nsound=off\n"}))
    m.gpio_input(95, 0)
    sp = STACK - 0x200

    m.task("ap")
    m.call_arm(A_PRINTF, m.strp("disarmed-line\r\n"), sp=sp)
    h.eq("".join(chr(t[1]) for t in m.calls("uart")), "disarmed-line\r\n", "LOG: printf goes out the stock UART path")
    h.eq(m.reg(UC_ARM_REG_SP), sp, "LOG: printf returns with sp intact")

    m.task("periph")
    m.reset()
    m.call_arm(0x22238, sp=sp, until=0x2223C)
    h.eq(m.names(), ["seg_refresh"], "D1: seg_refresh only")
    h.eq(m.reg(UC_ARM_REG_SP), sp, "D1: sp intact")

    for en, want in ((0, []), (1, ["vbus"])):
        m.poke8(B_USB_POLL_EN, en)
        m.reset()
        m.call_arm(0x2212C, sp=sp, until=0x2213C)
        h.eq(m.names(), want, f"G3a/G3b: poll enable {en}, {'VBUS poll only' if en else 'no poll'}")

    m.reset()
    plug(m)
    h.eq([t[2] for t in m.calls("qsend")], [0x8003], "G1/L2: stock insert path posts the base image's webcam init")
    m.reset()
    m.call_arm(0x1F4C0, sp=sp, until=0x1F4C4)
    h.eq(m.names(), ["usb_off"], "G2: stock 0xbde60 only")
    h.eq(m.reg(UC_ARM_REG_SP), sp, "G2: sp intact")

    m.reset()
    m.call_arm(0x1DD9C, sp=sp, regs={UC_ARM_REG_R5: 0x1C, UC_ARM_REG_R4: 0, UC_ARM_REG_R3: 0x1216E1}, until=0x1DDB8)
    h.eq((m.reg(UC_ARM_REG_SP), stv(m, ST_TOGGLE)), (sp, 0), "K1: straight to 0x1ddb8, no toggle")

    led = m.sym["led"]
    m.poke8(led + LED_OFF, 1)  # as if led=off were already applied
    m.call_arm(A_GPIO_SET, 43, 1)
    m.call_arm(A_GPIO_SET, 95, 1)
    h.eq((m.gpio(43), m.gpio(95)), (1, 1), "L1: gpio_set passes 43 and 95 through")
    h.eq((m.u8(led + LED_WANT43), m.u8(led + LED_WANT95)), (0, 0), "L1: nothing recorded")
    m.gpio_input(95, 1)
    h.eq(m.call_arm(0x1F5EC, 0x5F, sp=sp, until=0x1F5F0), 1, "L3: GPIO 95 read is the real pin")
    m.gpio_input(95, 0)
    m.poke8(led + LED_OFF, 0)

    m.task("ap")
    m.reset()
    h.eq(m.call_arm(0x22958, 3, sp=sp), 0xFFFFFFFF, "S1: chime 3 returns -1")
    h.eq(stv(m, ST_SOUND), 3, "S1: and is parked in sound_pending")
    h.not_called("play", "S1: body not entered for the chime", m=m)
    h.eq(m.call_arm(0x22958, 1, sp=sp), 0, "S1: id 1 plays as stock")
    h.eq(m.reg(UC_ARM_REG_SP), sp, "S1: sp intact")

    h.eq({n: m.hits[n] for n in V5_ENTRIES if m.hits[n]}, {}, "no payload C entered from any hook")

    m.task("usb")
    m.reset()
    m.call("v5_eject_cb", 0)
    h.called("eject", "G6: stock 0xbe230 runs", m=m)
    h.eq(stv(m, ST_RELEASED), 0, "G6: host_released not set")
    h.eq(stv(m, ST_EJECTING), 0, "G6: ejecting not set")

    print("  arming needs st.booted and [0x123cfc] == 1")
    m.task("periph")
    for booted, upd, why in (
        (1, 0, "SET ran, 0x3e3b0 not yet"),
        (1, 2, "updater started"),
        (0, 1, "mount found no update but SET never ran"),
    ):
        m.poke8(m.sym["st"] + ST_BOOTED, booted)
        m.poke8(B_UPD_CHECKED, upd)
        m.reset()
        m.call_arm(0x22238, sp=sp, until=0x2223C)
        h.eq(m.names(), ["seg_refresh"], f"booted {booted}, [0x123cfc] {upd} ({why}): disarmed")
    m.poke8(m.sym["st"] + ST_BOOTED, 0)
    m.poke8(B_UPD_CHECKED, 0)
    mount(m)
    h.eq(stv(m, ST_BOOTED), 1, "load_settings sets st.booted")
    m.reset()
    tick(m)
    h.called("v5_tick", "armed: D1 reaches v5_tick", m=m)
    m.poke8(B_USB_POLL_EN, 0)
    m.reset()
    m.call_arm(0x2212C, sp=sp, until=0x2213C)
    n = m.names()
    h.ok("vbus" in n and "v5_usb_tick" in n, "armed: G3a polls without the stock enable", f"({n})")
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
    h.eq(m.hits["logcap"], 1, "printf reached logcap through the 0x535e0 hook")
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
    probe = 0xE3270  # a .data word that is not 0x1234
    m = boot(cmd=f"poke {probe:#x} 0x1234\ncall 0x1fb3c\nmsg 0x7007\nkey shutter\npeek 0xe6828 4\nlog hi\n")
    before = m.u32(probe)
    tick(m)
    tick(m, 2100)
    h.eq(m.u32(probe), before, "poke stores nothing")
    h.not_called("reboot", "call does not reach 0x1fb3c", m=m)
    h.eq(m.calls("apq"), [], "msg and key post nothing")
    for c in ("poke", "call", "msg", "key", "peek", "log"):
        h.has(f"cmd: bad {c}", m=m)
    h.ok("000e6828:" not in log(m), "peek dumps nothing")

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
        (m.poke32 if wide else m.poke8)(addr, val)
        tick(m, 5000)
        h.ok("unmount" not in m.names() and not m.calls("apq"), f"{name}: no unmount, no post", f"({m.names()})")


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
        h.eq(m.call_arm(0x22958, 2, sp=STACK - 0x200), 0xFFFFFFFF, f"{cfg.strip()}: chime 2 returns -1 before settings")
        h.eq(stv(m, ST_SOUND), 2, f"{cfg.strip()}: and is parked in sound_pending")
        h.eq(m.call_arm(0x22958, 7, sp=STACK - 0x200), 0, f"{cfg.strip()}: id 7 plays as stock")
        h.eq([t[1] for t in m.calls("play")], [7], f"{cfg.strip()}: only id 7 reached the body")
        h.not_called("act_sound_gate", f"{cfg.strip()}: no C before settings", m=m)
        mount(m)
        m.reset()
        tick(m)
        played = [t[1] for t in m.calls("play")]
        h.eq(played, [2] if plays else [], f"{cfg.strip()}: chime {'replayed' if plays else 'dropped'}")
        h.eq(stv(m, ST_SOUND), 0, "sound_pending cleared")


def t_hook_sound(h):
    h.section = "seam S1 0x22958"
    print("\nseam S1: play_effect entry 0x22958")
    sp = STACK - 0x200
    off = boot("sound=off\n")
    off.task("ap")
    g = off.watch_writes(GPIO + 0x40, GPIO + 0x60)  # bank 2 holds GPIO 44
    r = off.call_arm(0x22958, 1, sp=sp)
    h.eq(r, 0xFFFFFFFF, "blocked id returns -1")
    h.eq(off.reg(UC_ARM_REG_SP), sp, "sp intact")
    h.called("act_sound_gate", "went through act_sound_gate", m=off)
    h.not_called("play", "play_effect body never entered", m=off)
    h.eq(g, [], "no write to GPIO 44's bank")
    h.eq(off.gpio(44), 0, "GPIO 44 low")

    on = boot("sound=on\n")
    on.task("ap")
    r4 = 0x44444444
    on.call_arm(0x22958, 5, sp=sp, regs={UC_ARM_REG_R4: r4}, until=0x2295C)
    h.eq(on.reg(UC_ARM_REG_R0), 5, "allowed id falls through to 0x2295c with r0 = id")
    h.eq(on.reg(UC_ARM_REG_SP), sp - 20, "with the displaced push {r4-r7,lr} done")
    h.eq(on.u32(sp - 4), RET, "pushed lr is the caller's return address")
    h.eq(on.u32(sp - 20), r4, "pushed r4 is the caller's r4")
    on.reset()
    r = on.call_arm(0x22958, 5, sp=sp)
    h.eq(r, 0, "and play_effect returns 0")
    h.eq([t[1] for t in on.calls("play")], [5], "body ran once with id 5")
    h.eq(on.gpio(44), 1, "amp opened by the body")
    h.eq(on.reg(UC_ARM_REG_SP), sp, "sp intact after the full call")


# ---------------------------------------------------------------- LED
def t_led_gate(h):
    h.section = "LED gate L1"
    print("\nLED gate at gpio_set 0x43224")
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
    m.call_arm(0x1DD9C, sp=sp, regs={UC_ARM_REG_R5: 0x1C, UC_ARM_REG_R4: 0, UC_ARM_REG_R3: 0x1216E1}, until=0x1DDB8)
    h.called("act_long_select", "count 0x1c reaches act_long_select", m=m)
    h.eq(m.reg(UC_ARM_REG_SP), sp, "ends at 0x1ddb8 with sp as found")
    h.eq(stv(m, ST_TOGGLE), 1, "toggle requested")
    tick(m, 300)
    h.eq((cfgv(m, CFG_DISPLAY), cfgv(m, CFG_LED)), (0, 0), "next tick turns display and LED off")
    m.reset()
    m.call_arm(0x1DD9C, sp=sp, regs={UC_ARM_REG_R5: 0x10, UC_ARM_REG_R4: 0, UC_ARM_REG_R3: 0x1216E1}, until=0x1DDA8)
    h.not_called("act_long_select", "short count falls through to 0x1dda8 untouched", m=m)


# ---------------------------------------------------------------- USB
def plug(m, sp=STACK - 0x200):
    """G1: VBUS state 0 with an insert edge, entered at 0x1f540, lands on 0x1f61c."""
    m.task("periph")
    regs = {r: 0x1000 + i for i, r in enumerate(CALLEE_SAVED)}
    m.call_arm(0x1F540, sp=sp, regs=regs, until=0x1F61C)
    return all(m.reg(r) == v for r, v in regs.items()) and m.reg(UC_ARM_REG_SP) == sp


def t_usb_plugin(h):
    h.section = "USB personality"
    print("\npersonality per switch position, through G1 0x1f540")
    for mode, want, name in ((0, 0x8001, "photo=drive"), (1, 0x8003, "video=webcam"), (2, 0x8003, "metering=webcam")):
        m = boot(CFG_DEFAULT, mode=mode)
        m.gpio_input(95, 0)
        intact = plug(m)
        posts = [(t[1], t[2]) for t in m.calls("qsend")]
        h.eq(posts, [(USBQ_H, want)], f"{name} posts {want:#x} to the USB queue")
        h.ok(intact, f"{name}: lands on 0x1f61c with sp and r4-r11 intact")
    m = boot("usb_video=drive\n", mode=1)
    plug(m)
    h.eq([t[2] for t in m.calls("qsend")], [0x8001], "usb_video=drive honoured")
    h.called("flush", "drive: camera cache flushed before the host reads the card", m=m)

    print("  webcam start clears the SET_CONFIG callback a drive init left behind")
    for mode, want in ((1, 0), (0, 0x3E3DC)):
        m = boot(CFG_DEFAULT, mode=mode)
        m.gpio_input(95, 0)
        m.poke32(W_MSDC_CB, 0x3E3DC)  # as usb_msdc_init leaves it (0x3e898 -> 0x4d488)
        plug(m)
        h.eq(m.u32(W_MSDC_CB), want, f"mode {mode}: [0x124254] = {want:#x}")

    print("  unplug through G2 0x1f4c0")
    for mode, want, off in ((0, [], True), (1, [0x8005], False)):
        m = boot(CFG_DEFAULT, mode=mode)
        plug(m)
        m.reset()
        m.call_arm(0x1F4C0, sp=STACK - 0x200, until=0x1F4C4)
        h.eq([t[2] for t in m.calls("qsend")], want, f"mode {mode}: unplug posts {want}")
        h.eq("usb_off" in m.names(), off, f"mode {mode}: stock 0xbde60 {'runs' if off else 'skipped'}")
        h.eq(stv(m, ST_STALE), 0, f"mode {mode}: card not stale")
        h.eq(m.reg(UC_ARM_REG_SP), STACK - 0x200, f"mode {mode}: returns to 0x1f4c4 with sp intact")
    m = boot()
    m.call_arm(0x1F4C0, sp=STACK - 0x200, until=0x1F4C4)
    h.called("usb_off", "unplug with nothing posted falls back to 0xbde60", m=m)

    print("  charger unplug in the drive personality: the camera keeps the card")
    m = boot(CFG_DEFAULT, mode=0)
    m.gpio_input(95, 0)
    plug(m)
    m.task("periph")
    h.eq(m.call("fs_ready"), 0, "fs_ready() 0 while the drive personality is up")
    m.call_arm(0x1F4C0, sp=STACK - 0x200, until=0x1F4C4)
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
    print("\nplug-in while capturing defers to the tick (G3b 0x22354)")
    m = boot(CFG_DEFAULT, mode=1)
    m.gpio_input(95, 1)
    plug(m)
    h.not_called("qsend", "nothing posted while the busy pin is high", m=m)
    h.eq(stv(m, ST_PENDING), 2, "want stashed in usb_pending")
    m.gpio_input(95, 0)
    m.poke8(B_USB_STATE, 1)
    m.poke8(B_MODE, 2)
    m.reset()
    m.call_arm(0x22354, sp=STACK - 0x104, until=0x22358)
    n = m.names()
    h.ok(
        "vbus" in n and "v5_usb_tick" in n and n.index("vbus") < n.index("v5_usb_tick"),
        "stock VBUS poll first, then v5_usb_tick",
        f"({n})",
    )
    h.eq([t[2] for t in m.calls("qsend")], [0x8003], "tick posts once the bus is free")
    h.eq(m.reg(UC_ARM_REG_SP), STACK - 0x104, "returns to 0x22358 with sp intact")

    print("  plug-in before the first mount result")
    m = boot(mode=0)
    m.poke8(B_USB_POLL_EN, 0)
    plug(m)
    h.not_called("qsend", "deferred until the mount result is in", m=m)
    m.poke8(B_USB_POLL_EN, 1)
    m.poke8(B_USB_STATE, 1)
    m.call_arm(0x22354, sp=STACK - 0x100, until=0x22358)
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
    m.call("v5_eject_cb", 0)
    h.eq(seen, [(1, 0)], "ejecting set before the stock uninit runs")
    h.eq((stv(m, ST_EJECTING), stv(m, ST_RELEASED)), (0, 1), "then host_released set, ejecting cleared")
    h.called("eject", "stock 0xbe230 still runs", m=m)
    h.ok(
        not any(t[0] in ("open", "write", "stat", "unlink", "mount", "sd_init") for t in m.trace),
        "no file I/O in the USB task",
    )


def t_hook_eject(h):
    h.section = "seam eject e2e"
    print("\nseam: eject handback end to end, stock 0x4e708 -> v5_eject_cb -> tick -> LUN 1")
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
    r = m.call_arm(A_LUN_REG, 1, 0, 0, SD_OPS)  # usb_msdc_init registers LUN 0 (0x3e834)
    h.eq(r, 0, "stock LUN register 0x4e89c ok")
    h.eq(m.u32(LUN0 + 0x40), m.sym["v5_eject_cb"], "LUN+0x40 holds the patched G6 pointer")
    h.eq(m.u16(H_LUN0), 1, "LUN 0 status 1")
    # the host writes CMD.TXT while it owns the card, then ejects
    m.card.f["CMD.TXT"] = bytearray(b"set sound=off\n")
    m.poke32(MSC_CTX + 4, LUN0)
    m.poke8(MSC_CTX + 0x51, 0)
    m.poke8(MSC_CTX + 0x57, 2)  # START STOP UNIT, CDB[4] LoEj
    m.reset()
    m.call_arm(A_START_STOP)
    h.called("v5_eject_cb", "0x4e740 blx reached v5_eject_cb", m=m)
    h.called("eject", "which ran the stock 0xbe230 (SD uninit)", m=m)
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
        [(a, s, v) for a, s, v, _ in lun], [(H_LUN0, 2, 1)], "handback: one 16-bit write of 1 to 0x124278, nothing else"
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
    h.not_called("sd_init", "no retry while a capture is busy", m=m)
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


# ---------------------------------------------------------------- cards
def t_no_card(h):
    h.section = "no card"
    print("\nno card: settings never load, the patch stays disarmed and the camera runs as the base image")
    m = Machine(IMAGE, SYM, Card({}), present=False)
    m.poke8(B_MOUNT_FAIL, 1)  # 0x2e840, photo mount-fail handler
    m.poke8(B_UPD_CHECKED, 1)  # 0x3e3b0 runs after a failed mount too
    m.poke8(B_USB_POLL_EN, 0)
    m.poke32(W_APP_STATE, 0x102)
    tick(m, 5000)
    tick(m, 5000)
    h.eq(stv(m, ST_BOOTED), 0, "booted stays 0")
    h.ok(not any(t[0] in ("open", "stat", "write", "unlink", "mount") for t in m.trace), "no file call")
    m.gpio_input(95, 0)
    plug(m)
    h.eq([t[2] for t in m.calls("qsend")], [0x8003], "plug-in posts the base image's webcam init (pool word 0x8003)")
    m.task("ap")
    m.call_arm(0x22958, 1, sp=STACK - 0x200)
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
    print("\nseam LOG: 0x535e0 with r4 = PRINT_BUF")
    m = boot()
    tick(m)
    sp = STACK - 0x200
    line = b"seam-line-535e0\r\n"
    m.put(A_PRINT_BUF, line + b"\0")
    before = m.ring()
    m.reset()
    m.call_arm(0x535E0, sp=sp, regs={UC_ARM_REG_R4: A_PRINT_BUF}, until=0x535A8)
    got_sp = m.reg(UC_ARM_REG_SP)
    h.eq(m.calls("logcap")[:1] and m.calls("logcap")[0][1], A_PRINT_BUF, "logcap entered with r0 = PRINT_BUF")
    h.eq(got_sp, sp, "returns to 0x535a8 with sp intact")
    h.eq(m.ring() - before, len(line), "line queued")
    # the rest of printf's epilogue with a real frame: 0xc locals, r4 r5 lr, 4 varargs
    m.poke32(sp + 0xC, 0x4444)
    m.poke32(sp + 0x10, 0x5555)
    m.poke32(sp + 0x14, RET)
    m.call_arm(0x535A8, sp=sp, lr=0)
    h.eq((m.reg(UC_ARM_REG_SP), m.reg(UC_ARM_REG_R4)), (sp + 0x28, 0x4444), "printf's epilogue then pops its own frame")
    m.reset()
    m.call_arm(A_PRINTF, m.strp("whole-printf %d\r\n"), 42, sp=sp)
    h.eq(m.reg(UC_ARM_REG_SP), sp, "a whole stock printf call returns with sp intact")
    m.call("log_flush")
    h.has("whole-printf 42", m=m)

    print("seam D1: 0x22238 in the peripheral task")
    m = boot()
    regs = {r: 0x2000 + i for i, r in enumerate(CALLEE_SAVED)}
    for sp in (STACK - 0x100, STACK - 0x104):
        m.reset()
        m.call_arm(0x22238, sp=sp, regs=regs, until=0x2223C)
        n = m.names()
        h.ok(
            "seg_refresh" in n and "v5_tick" in n and n.index("seg_refresh") < n.index("v5_tick"),
            f"sp%8={sp % 8}: seg_refresh 0x21250, then v5_tick",
            f"({n[:6]})",
        )
        h.eq(m.sp_at.get("v5_tick", 1) % 8, 0, f"sp%8={sp % 8}: v5_tick entered 8-aligned")
        h.eq(m.reg(UC_ARM_REG_SP), sp, f"sp%8={sp % 8}: back at 0x2223c with sp intact")
        h.ok(all(m.reg(r) == v for r, v in regs.items()), f"sp%8={sp % 8}: r4-r11 intact")

    print("seam SET: 0x37df4 at the VERSION.TXT writer's epilogue")
    m = Machine(IMAGE, SYM, Card({"C100.TXT": "led=off\n"}))
    m.task("storage")
    sp = STACK - 0x200
    m.poke32(sp + 0x58, 0xCAFE0004)
    m.poke32(sp + 0x5C, RET)
    stops = {}

    def at_ret(uc, a, size, _):
        stops.setdefault("sp", uc.reg_read(UC_ARM_REG_SP))

    m.uc.hook_add(UC_HOOK_CODE, at_ret, begin=0x37DF8, end=0x37DF8)
    m.call_arm(0x37DF4, sp=sp, regs={UC_ARM_REG_R4: 0x1234})
    h.called("load_settings", "load_settings ran", m=m)
    h.eq(m.sp_at.get("load_settings", 0) % 8, 0, "load_settings entered 8-aligned")
    h.eq(stops.get("sp"), sp + 0x58, "reached 0x37df8 after the displaced add sp,sp,#0x58")
    h.eq(m.reg(UC_ARM_REG_R4), 0xCAFE0004, "0x37df8 popped the caller's r4")
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
    print("\nseam: deepest stack from the 0x22238 stub through v5_tick")
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
        m.call_arm(0x22238, sp=sp0, until=0x2223C, limit=20_000_000)
        m.uc.hook_del(hk)
        depth = max(sp0 - low[0], sp0 - m.stack_low)
        results[label] = (depth, where[0])
        for name, csp in m.call_sp.items():
            at_call[name] = max(at_call.get(name, 0), sp0 - csp)
    worst = max(d for d, _ in results.values())
    for label, (depth, pc) in results.items():
        print(f"  {label}: {depth} B below the 0x22238 call, deepest sp at pc {pc:#x}")
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
    vs_alt, idx, sp = 0x123D04, 0x12178C, STACK - 0x200
    for alt, passes in ((0, True), (3, False)):
        m = boot()
        m.poke32(vs_alt, alt)
        what = f"alt {alt}"
        for site in (0x2EA90, 0x36F4C, 0x36F7C):
            m.poke8(idx, 1)
            m.call_arm(site, 1, sp=sp, until=site + 4)
            h.eq(m.u8(idx) != 1, passes, f"{what}: {site:#x} {'steps' if passes else 'leaves'} the aspect index")
            h.eq(m.reg(UC_ARM_REG_SP), sp, f"{what}: {site:#x} sp intact")
        # 0x356d8 with no record flags: ands sets Z, so the beq at 0x356e0 is taken
        stop = 0x35858 if passes else 0x356E4
        try:
            m.call_arm(0x356D8, sp=sp, regs={UC_ARM_REG_R3: 0, UC_ARM_REG_R4: 7}, until=stop, limit=50)
            got = stop
        except EmuError as e:
            got = str(e)
        h.eq(got, stop, f"{what}: record start goes to {stop:#x}")
        h.eq(m.u8(B_REC_FLAGS), 0, f"{what}: record flags untouched")
        h.clean(m)


TESTS = (
    t_image,
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
    t_no_card,
    t_empty_card,
    t_hook_chain,
    t_reentry,
    t_stack,
    t_v6,
)


def main():
    global IMAGE, SYM, BASE
    IMAGE, SYM = sys.argv[1], sys.argv[2]
    BASE = sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, "..", "obj", "base.bin")
    h = Harness(None)
    probe = Machine(IMAGE, SYM)
    print(f"payload end {probe.sym['__v5_end']:#x}, gap left {GAP_END - probe.sym['__v5_end']} B")
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
