# Canary tests under emulation (docs/hooks.md). usage, from the repo root after CANARY=1 ./build.sh:
#   uv run --group test python tests/test_canary.py build-canary/GP_CARDVR_UPGRADE.BIN obj-canary/v5.sym [BASE]
# BASE defaults to obj-canary/base.bin, the image tools/patch.py patched.
# Not with python -I: it drops the script directory and the imports fail.
import os
import struct
import sys

import test_v5
from emu import A_GPIO_SET, A_PRINTF, B_USB_POLL_EN, OVL_FLASH, STACK, Card, EmuError, Harness, Machine
from test_v5 import (
    B_UPD_CHECKED,
    HOOKMAP,
    LED_OFF,
    LED_WANT43,
    LED_WANT95,
    ST_BOOTED,
    ST_LOADED,
    ST_RELEASED,
    ST_SOUND,
    ST_TOGGLE,
    V5_ENTRIES,
    mount,
    plug,
    stv,
)
from unicorn import UcError
from unicorn.arm_const import UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_SP

HERE = os.path.dirname(os.path.abspath(__file__))
# the full hook map without G4, the V6 and the XU rows (tools/patch.py with CANARY=1); the 4 KB
# stack and heap rows are in both
CANARY_MAP = [r for r in HOOKMAP if r[0] not in ("G4", "XU") and not r[0].startswith("V6")]


def line(m, chime):
    crc = struct.unpack_from("<I", m.raw, OVL_FLASH + 8)[0]
    return f"canary 2 ovl {crc:08x} chime {chime}\r\n"


def t_image(h):
    test_v5.IMAGE, test_v5.SYM, test_v5.BASE, test_v5.HOOKMAP = IMAGE, SYM, BASE, CANARY_MAP
    test_v5.t_image(h)


def t_heap(h):
    test_v5.t_heap(h)


def t_loader(h):
    test_v5.t_loader(h, armed=False)


def t_disarmed(h):
    h.section = "disarmed hooks"
    print("\nbefore any mount, every hook runs only its displaced stock instruction")
    m = Machine(IMAGE, SYM, Card({}))
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
    m.poke8(led + LED_OFF, 1)
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
    m.call("eject_stub", 0)
    h.called("eject", "G6: stock 0xbe2d4 runs", m=m)
    h.not_called("v5_eject_cb", "G6: the overlay's v5_eject_cb not entered", m=m)
    h.eq(stv(m, ST_RELEASED), 0, "G6: host_released not set")
    h.clean(m)


def t_set(h):
    h.section = "SET: one line, gate stays shut"
    print("\nSET loads the overlay, which appends one line per mount and never arms")
    m = Machine(IMAGE, SYM, Card({}))
    sp = STACK - 0x200
    m.task("ap")
    m.call_arm(0x22A80, 3, sp=sp)  # the chime S1 parks on the way up
    m.reset()
    m.stack_low = STACK
    top = mount(m)
    h.eq(stv(m, ST_LOADED), 1, "the loader read and checked the overlay")
    h.eq(m.card.get("LOG.TXT").decode("latin1"), line(m, 3), "first mount creates LOG.TXT with the line")
    h.eq([t[2] for t in m.calls("open")], [1, 0x601], "open existing, then create")
    h.called("flush", "drive flushed", m=m)
    h.eq(m.reg(UC_ARM_REG_R4), 0xC0DE0004, "r4 intact through the stub (0x37fa4 pops it)")
    h.eq(m.fds, {}, "no file left open")
    h.eq(m.live, {}, "no heap used")
    h.eq(stv(m, ST_BOOTED), 0, "st.booted stays 0")
    # the stub drops the writer's 0x58 B frame before calling in; the modelled stock file
    # and NOR calls' own frames are not counted
    above = m.stack_low - top
    h.ok(
        above >= 0,
        f"SET path (loader and line) stays inside the stack the writer held at 0x37fa0 ({above} B above its sp)",
        f"(lowest write {m.stack_low:#x})",
    )

    m.reset()
    mount(m)
    h.eq(m.card.get("LOG.TXT").decode("latin1"), line(m, 3) * 2, "second mount appends")
    h.eq(m.calls("nv_read"), [], "second mount: overlay already loaded, no NOR read")
    h.eq(m.u8(B_UPD_CHECKED), 1, "[0x123d34] = 1, as after the first mount call that started no update")

    print("  after the mount, every hook still runs disarmed")
    m.task("periph")
    m.reset()
    m.call_arm(0x22360, sp=sp, until=0x22364)
    h.eq(m.names(), ["seg_refresh"], "D1: seg_refresh only")
    m.poke8(B_USB_POLL_EN, 0)
    m.reset()
    m.call_arm(0x22254, sp=sp, until=0x22264)
    h.eq(m.names(), [], "G3a: stock poll gate, no poll")
    m.reset()
    plug(m)
    h.eq([t[2] for t in m.calls("qsend")], [0x8003], "G1: the base image's webcam init")
    m.task("ap")
    m.reset()
    h.eq(m.call_arm(0x22A80, 2, sp=sp), 0xFFFFFFFF, "S1: chime still parked")
    h.eq(m.call_arm(0x22A80, 1, sp=sp), 0, "S1: id 1 plays")
    m.task("usb")
    m.reset()
    m.call("eject_stub", 0)
    h.not_called("v5_eject_cb", "G6: eject_stub stays on the stock callback", m=m)
    entered = {n: m.hits[n] for n in V5_ENTRIES if m.hits[n]}
    h.eq(entered, {}, "no payload C entered apart from load_settings")
    h.clean(m)

    m = Machine(IMAGE, SYM, Card({"LOG.TXT": "old\r\n"}))
    mount(m)
    h.eq(
        m.card.get("LOG.TXT").decode("latin1"),
        "old\r\n" + line(m, 0),
        "existing LOG.TXT kept, line appended; chime 0 when none was parked",
    )
    h.clean(m)


TESTS = (t_image, t_heap, t_loader, t_disarmed, t_set)


def main():
    global IMAGE, SYM, BASE
    IMAGE, SYM = sys.argv[1], sys.argv[2]
    BASE = sys.argv[3] if len(sys.argv) > 3 else os.path.join(HERE, "..", "obj-canary", "base.bin")
    test_v5.IMAGE, test_v5.SYM, test_v5.BASE = IMAGE, SYM, BASE
    h = Harness(None)
    for t in TESTS:
        try:
            t(h)
        except (EmuError, UcError, AssertionError, KeyError, ValueError) as e:
            h.ok(False, f"{t.__name__} crashed", f"({type(e).__name__}: {e})")
    return h.report()


if __name__ == "__main__":
    sys.exit(main())
