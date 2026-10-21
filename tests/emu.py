# End-to-end emulation of the patch: hooks, the overlay loader, C, stock calls,
# virtual card. Imported by test_v5.py and test_canary.py. Image is a built image
# (build/GP_CARDVR_UPGRADE.BIN, or build/TEST-ONLY.bin padded to 2 MiB), sym is
# obj/v5.sym.
#
# The card is a dict of names to bytes. Stock file, heap, HT1621, sound body,
# queue, USB and NOR page-read calls are modelled in Python and recorded in
# Machine.trace; each model returns what the disassembly of the real function
# returns (address and return values noted above the less obvious f_* models).
# The NOR is the image file, byte for byte, as the updater writes it, then 0xFF
# to 2 MiB. Real stock code that runs unmodelled: printf 0x53758 (so its lines go
# through the LOG hook at 0x537b4 into logcap), newlib vsnprintf and the libc
# thunks, gpio_set 0x433f4 and gpio_get 0x433b8 against the GPIO MMIO at
# 0xC0000000 (output +4, input +0 per bank of 16), card id 0x262c0, the MSC LUN
# register 0x4ea70, START STOP UNIT 0x4e8dc, the D-cache clean 0x40158 (its cp15
# ops run as no-ops in Unicorn; the calls are recorded) and the heap setup
# 0x5ccb4.
#
# The overlay region 0xff0000..0x1000000 starts filled with UDF words, so a jump
# into an overlay that was not loaded faults instead of running zeros.
#
# Tasks: the payload asks "am I the peripheral task" by comparing pxCurrentTCB
# with the peripheral task handle, so Machine.task() sets both words.
import struct

from unicorn import UC_ARCH_ARM, UC_HOOK_BLOCK, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_MODE_ARM, Uc, UcError
from unicorn.arm_const import (
    UC_ARM_REG_CPSR,
    UC_ARM_REG_LR,
    UC_ARM_REG_PC,
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
    UC_ARM_REG_R11,
    UC_ARM_REG_R12,
    UC_ARM_REG_SP,
    UC_CPU_ARM_926,
)

B = 0x10200
SIZE = 1841152
# emulator-only addresses, below the overlay reservation 0xff0000..0x1000000
RET = 0x00F10000  # magic return address, emulation stops here
TRAMP = 0x00F10100  # injected calls return here
STACK = 0x00F00000
STACK_LO = STACK - 0x10000
HEAP = 0x00400000
BX_LR = bytes.fromhex("1eff2fe1")
CPSR_TASK = 0x1F  # SYS mode, ARM, IRQs enabled: how a FreeRTOS task runs
REGS = (
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
    UC_ARM_REG_R11,
    UC_ARM_REG_R12,
)
CALLEE_SAVED = REGS[4:12]  # r4-r11

# stock functions
A_OPEN, A_READ, A_WRITE, A_LSEEK, A_CLOSE = 0x9E608, 0x9E67C, 0x9E9C8, 0x9E4B4, 0x9DC7C
A_UNLINK, A_STAT, A_FLUSH, A_MOUNT, A_UNMOUNT = 0x9E96C, 0x9E894, 0x9AC6C, 0x9DF4C, 0x9DF90
A_RENAME, A_SDINIT, A_MALLOC, A_FREE, A_PRINTF = 0x9E6EC, 0xADB6C, 0x5374C, 0x53754, 0x53758
A_TICKS, A_RTC, A_HT_CMD, A_LED_LEVEL = 0x5DEE4, 0x4536C, 0x4A01C, 0x1FCAC
A_GPIO_SET, A_MSG_POST, A_QSEND = 0x433F4, 0x42C, 0x5D1B4
A_PLAY_BODY, A_SEG, A_VBUS, A_USB_OFF = 0x22A84, 0x21318, 0x1F3D4, 0xBDF04
A_EJECT, A_REBOOT, A_POWER_OFF, A_FS_TAKE, A_FS_GIVE = 0xBE2D4, 0x1FBAC, 0x262E8, 0xA0EB0, 0xA0ECC
A_CS_ENTER, A_CS_EXIT, A_DELAY, A_CSW = 0x5C984, 0x5C9A4, 0x5E210, 0x4D9D4
A_LUN_REG, A_START_STOP, A_UART_PUT = 0x4EA70, 0x4E8DC, 0x48F14
A_NV_READ, A_DCLEAN, A_DCLEAN_LINE = 0x594B8, 0x40158, 0x40160  # nv_read_page; clean D range, its per-line mcr
A_SD_UNINIT = 0xB3844
A_WDT_KICK = 0x47014  # 0xa005 to 0xd000002c, the SoC watchdog kick
A_MSGQ_SEND = 0x5C320  # msgQSend(q, item, ticks): 0x3e5e0 posts uvc_task's start/stop with it
A_PRINT_BUF, GAP, GAP_END = 0x124478, 0xE6968, 0xE8000

# stock variables
B_CARD_OK, B_MOUNT_FAIL, B_USB_POLL_EN = 0x12199D, 0x121716, 0x131FC0
B_MODE, B_USB_STATE, W_APP_STATE = 0x121706, 0x12164A, 0x13288C
B_CAPTURE_BUSY, B_REC_FLAGS, B_F_HELD = 0x12191E, 0x132ABB, 0x1215A7
B_CARD_TYPE = 0x121814  # read by card id 0x262c0, 0xff = no card
LUN0, H_LUN0, MSC_CTX, SD_OPS = 0x1242AC, 0x1242B8, 0x1350E0, 0xE0948
CUR_TCB, PERIPH_TCB, APQ_PTR, USBQ_PTR = 0x126B74, 0x1929AC, 0x132A14, 0x123D6C
W_NV_MUTEX, W_NV_DIR = 0x124878, 0x12487C  # set by nv init 0x59fd8 before any mount
GPIO = 0xC0000000

# overlay (src/loader.c, tools/patch.py)
OVL_BASE, OVL_TOP, OVL_FLASH, OVL_MAGIC, OVL_HDR = 0xFF0000, 0x1000000, 0x100000, 0x4C564F43, 0x10
UDF = bytes.fromhex("fedeffe7")  # ARM udf; as Thumb, udf #0xfe
NV_MUTEX_OBJ, NV_DIR_OBJ = 0x00A03000, 0x00A04000
TCB = {"periph": 0x00A00000, "ap": 0x00A00100, "usb": 0x00A00200, "storage": 0x00A00300}
APQ_OBJ, USBQ_H = 0x00A01000, 0x00A02000

O_READ_ONLY, O_READ_WRITE, O_CREATE_TRUNC = 0x000, 0x001, 0x601


def arm_bl(at, target):
    return struct.pack("<I", 0xEB000000 | (((target - (at + 8)) >> 2) & 0xFFFFFF))


class EmuError(Exception):
    pass


class Card:
    def __init__(self, files=None):
        self.f = {k.upper(): bytearray(v if isinstance(v, bytes) else v.encode()) for k, v in (files or {}).items()}

    def get(self, name):
        return bytes(self.f.get(name.upper(), b""))

    def names(self):
        return sorted(self.f)


class Machine:
    def __init__(self, image, symfile, card=None, present=True):
        d = open(image, "rb").read()
        assert len(d) in (1841152, 0x200000), len(d)
        loadlen = struct.unpack_from("<I", d, 0x1002E)[0] * 0x200 - 0x200
        self.raw = d
        self.sym = {}
        for line in open(symfile):
            a, _, name = line.split()
            self.sym[name] = int(a, 16)
        self.card = card if card is not None else Card()
        self.trace = []
        self.violations = []  # model rules the payload broke: file I/O on a dead card, etc.
        self.ticks = 1000
        self.fds = {}
        self.next_fd = 3
        self.heap = HEAP
        self.live = {}  # malloc'd block -> size
        self.rtc = [7, 42, 13, 11]  # sec min hour day
        self.unmount_ret = 0
        self.mount_ret = 0
        self.unlink_ret = 0  # nonzero: unlink fails with it, as on a write-protected card
        self.sd_init_ret = 0  # nonzero: SD init fails with it
        self.qsend_ret = 1  # pdPASS
        # NOR = the image file, then erased flash to 2 MiB; tests corrupt it to make loads fail
        self.nor = bytearray(d[:SIZE]) + b"\xff" * (0x200000 - SIZE)
        self.nv_fail = set()  # NOR addresses whose page read fails (nv_read_page returns nonzero)
        self.dclean_lines = 0  # D-cache lines the fw clean routine has cleaned
        self.ovl_blocks = None  # trap_overlay()
        self.sd_up = present  # SD driver inited; eject and MSC uninit take it down
        self.mounted = present
        self.stack_low = STACK
        self.hits = {}
        self.sp_at = {}  # sp at the last entry of each watched address
        self.call_sp = {}  # lowest sp at entry to each modelled stock call
        self.injections = {}
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        uc.ctl_set_cpu_model(UC_CPU_ARM_926)  # ARMv5TE, as the GP22B: Thumb-2 encodings fault
        uc.mem_map(0, 0x1000000)
        uc.mem_write(0, d[B : B + loadlen])
        uc.mem_write(OVL_BASE, UDF * ((OVL_TOP - OVL_BASE) // 4))
        uc.mem_map(GPIO, 0x100000)
        uc.mem_write(RET, bytes.fromhex("feffffea"))
        uc.mem_write(TRAMP, bytes.fromhex("feffffea"))
        uc.reg_write(UC_ARM_REG_CPSR, CPSR_TASK)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._stack_write, begin=STACK_LO, end=STACK + 0x100)
        uc.hook_add(UC_HOOK_CODE, self._tramp, begin=TRAMP, end=TRAMP + 4)
        self.stubs = {}
        # void stock calls: record and return
        for a, n in (
            (A_HT_CMD, "ht_cmd"),
            (A_LED_LEVEL, "led_level"),
            (A_SEG, "seg_refresh"),
            (A_VBUS, "vbus"),
            (A_USB_OFF, "usb_off"),
            (A_POWER_OFF, "power_off"),
            (A_FS_TAKE, "fs_take"),
            (A_FS_GIVE, "fs_give"),
            (A_CS_ENTER, "cs_enter"),
            (A_CS_EXIT, "cs_exit"),
            (A_REBOOT, "reboot"),
            (A_CSW, "csw"),
            (A_UART_PUT, "uart"),
            (A_WDT_KICK, "wdt_kick"),
        ):
            self.stub(a, n, lambda m, *r: None)
        self.stub(A_OPEN, "open", self.f_open)
        self.stub(A_READ, "read", self.f_read)
        self.stub(A_WRITE, "write", self.f_write)
        self.stub(A_LSEEK, "lseek", self.f_lseek)
        self.stub(A_CLOSE, "close", self.f_close)
        self.stub(A_UNLINK, "unlink", self.f_unlink)
        self.stub(A_STAT, "stat", self.f_stat)
        self.stub(A_RENAME, "rename", self.f_rename)
        self.stub(A_FLUSH, "flush", self.f_flush)
        self.stub(A_MOUNT, "mount", self.f_mount)
        self.stub(A_UNMOUNT, "unmount", self.f_unmount)
        self.stub(A_SDINIT, "sd_init", self.f_sd_init)
        self.stub(A_SD_UNINIT, "sd_uninit", self.f_sd_uninit)
        self.stub(A_MALLOC, "malloc", self.f_malloc)
        self.stub(A_FREE, "free", self.f_free)
        self.stub(A_TICKS, "ticks", lambda m, *a: self.ticks)
        self.stub(A_DELAY, "delay", self.f_delay)
        self.stub(A_RTC, "rtc", self.f_rtc)
        self.stub(A_MSG_POST, "apq", self.f_msg_post)
        self.stub(A_QSEND, "qsend", self.f_qsend)
        self.stub(A_EJECT, "eject", self.f_eject)
        self.stub(A_NV_READ, "nv_read", self.f_nv_read)
        self.stub(A_MSGQ_SEND, "msgq_send", self.f_msgq_send)
        # play_effect past its entry push (0x22a84): open the amp the way the body does
        # (gpio_set(44, 1) at 0x22b38, through the L1 gate), then return 0 like its
        # success path, popping the frame the entry pushed.
        body = A_PLAY_BODY
        code = bytes.fromhex("2c00a0e30110a0e3") + arm_bl(body + 8, A_GPIO_SET) + bytes.fromhex("0000a0e3f080bde8")
        self.stub(body, "play", lambda m, *r: None, code=code)
        self.watch(A_PRINTF, "printf")
        self.watch(A_DCLEAN, "dclean")
        self.uc.hook_add(UC_HOOK_CODE, self._dclean_line, begin=A_DCLEAN_LINE, end=A_DCLEAN_LINE)
        self.watch(self.sym["v5_icache_inval"] + 4, "icache_inval")  # the mcr itself
        self.watch(self.sym["v5_set"] & ~1, "v5_set")
        for name in (
            "logcap",
            "v5_tick",
            "load_settings",
            "act_long_select",
            "act_sound_gate",
            "v5_usb_plugin",
            "v5_usb_unplug",
            "v5_usb_tick",
            "v5_eject_cb",
        ):
            self.watch(self.sym[name] & ~1, name)
        # boot state: the stock mount sets the card flag at 0x3db04 before the
        # VERSION.TXT writer (and the SET hook) runs; queues exist from InitTask;
        # nv init has run in the state_handling task (mutex, resource directory)
        self.poke32(W_NV_MUTEX, NV_MUTEX_OBJ)
        self.poke32(W_NV_DIR, NV_DIR_OBJ)
        self.poke32(PERIPH_TCB, TCB["periph"])
        self.poke32(APQ_PTR, APQ_OBJ)
        self.poke32(USBQ_PTR, USBQ_H)
        self.poke8(B_CARD_OK, 1 if present else 0)
        self.poke8(B_CARD_TYPE, 1 if present else 0xFF)
        self.task("periph")

    # ---- stubs and watches ----
    def stub(self, addr, name, fn, code=BX_LR):
        self.uc.mem_write(addr, code)
        self.stubs[addr] = name
        self.uc.hook_add(UC_HOOK_CODE, self._at(name, fn), begin=addr, end=addr)

    def _at(self, name, fn):
        def cb(uc, a, size, _):
            r = [uc.reg_read(x) for x in REGS[:4]]
            sp = uc.reg_read(UC_ARM_REG_SP)
            self.call_sp[name] = min(sp, self.call_sp.get(name, sp))
            self.trace.append((name, *r))
            out = fn(self, *r)
            if out is not None:
                uc.reg_write(UC_ARM_REG_R0, out & 0xFFFFFFFF)

        return cb

    def watch(self, addr, name):
        """Record entries to addr without changing what runs there."""
        self.hits[name] = 0

        def cb(uc, a, size, _):
            self.hits[name] += 1
            self.sp_at[name] = uc.reg_read(UC_ARM_REG_SP)
            self.trace.append((name, *[uc.reg_read(x) for x in REGS[:4]]))

        self.uc.hook_add(UC_HOOK_CODE, cb, begin=addr, end=addr)

    def _dclean_line(self, uc, a, size, _):
        self.dclean_lines += 1

    def trap_overlay(self):
        """From now on, record every basic block that starts inside the overlay region."""
        self.ovl_blocks = []

        def cb(uc, a, size, _):
            self.ovl_blocks.append(a)

        self.uc.hook_add(UC_HOOK_BLOCK, cb, begin=OVL_BASE, end=OVL_TOP - 1)

    def _stack_write(self, uc, access, addr, size, value, _):
        if addr < self.stack_low:
            self.stack_low = addr

    def watch_writes(self, lo, hi):
        """Every store into [lo, hi) from now on: (addr, size, value, pc)."""
        log = []

        def cb(uc, access, addr, size, value, _):
            log.append((addr, size, value & ((1 << (8 * size)) - 1), uc.reg_read(UC_ARM_REG_PC)))

        self.uc.hook_add(UC_HOOK_MEM_WRITE, cb, begin=lo, end=hi - 1)
        return log

    def task(self, name):
        """Run what follows as the peripheral, AP, USB or storage task."""
        self.poke32(CUR_TCB, TCB[name])

    # ---- running code ----
    def _run(self, start, until, args, sp, lr, regs, limit):
        uc = self.uc
        for reg, v in zip(REGS, args, strict=False):
            uc.reg_write(reg, v & 0xFFFFFFFF)
        for reg, v in (regs or {}).items():
            uc.reg_write(reg, v & 0xFFFFFFFF)
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_LR, lr)
        cpsr = uc.reg_read(UC_ARM_REG_CPSR)
        uc.reg_write(UC_ARM_REG_CPSR, cpsr | 0x20 if start & 1 else cpsr & ~0x20)
        irq = uc.reg_read(UC_ARM_REG_CPSR) & 0xC0
        # Unicorn picks the decode state from the address bit 0, not from CPSR:
        # masking it here silently runs Thumb code as ARM.
        try:
            uc.emu_start(start, until, count=limit)
        except UcError as e:
            pc = uc.reg_read(UC_ARM_REG_PC)
            raise EmuError(f"{e} at pc {pc:#x}, last calls {self.names()[-6:]}") from None
        pc = uc.reg_read(UC_ARM_REG_PC)
        if pc != until & ~1:
            why = f"returned to lr {lr:#x}" if pc == lr & ~1 and lr != until else f"instruction limit {limit}"
            raise EmuError(f"stopped at {pc:#x}, not {until:#x} ({why})")
        if uc.reg_read(UC_ARM_REG_CPSR) & 0xC0 != irq:
            self.violations.append(f"IRQ mask {irq:#x} -> {uc.reg_read(UC_ARM_REG_CPSR) & 0xC0:#x} after {start:#x}")
        return uc.reg_read(UC_ARM_REG_R0)

    def call(self, name, *args, sp=STACK, limit=3_000_000):
        return self._run(self.sym[name], RET, args, sp, RET, None, limit)

    def call_arm(self, addr, *args, until=RET, sp=STACK, lr=RET, regs=None, limit=3_000_000):
        return self._run(addr, until, args, sp, lr, regs, limit)

    def reg(self, r):
        return self.uc.reg_read(r)

    def inject(self, at, target, args=(), regs=None, sp_gap=0x40, before=None, after=None):
        """The first time execution reaches `at`, run `target(args)` as if an interrupt
        called it there, on the same stack below sp, then resume at `at` with every
        register as it was. `before`/`after` run in Python around the nested call."""
        st = {"armed": True}

        def cb(uc, a, size, _):
            if not st["armed"]:
                return
            st["armed"] = False
            assert "resume" not in self.injections, "one injection at a time"
            saved = {r: uc.reg_read(r) for r in REGS + (UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_CPSR)}
            thumb = saved[UC_ARM_REG_CPSR] & 0x20
            self.injections["resume"] = (at | (1 if thumb else 0), saved, after)
            sp = (saved[UC_ARM_REG_SP] - sp_gap) & ~7
            if before:
                sp = before(self, sp)
            for reg, v in zip(REGS, args, strict=False):
                uc.reg_write(reg, v & 0xFFFFFFFF)
            for reg, v in (regs or {}).items():
                uc.reg_write(reg, v & 0xFFFFFFFF)
            uc.reg_write(UC_ARM_REG_SP, sp)
            uc.reg_write(UC_ARM_REG_LR, TRAMP)
            cpsr = saved[UC_ARM_REG_CPSR]
            uc.reg_write(UC_ARM_REG_CPSR, cpsr | 0x20 if target & 1 else cpsr & ~0x20)
            uc.reg_write(UC_ARM_REG_PC, target)

        self.uc.hook_add(UC_HOOK_CODE, cb, begin=at & ~1, end=at & ~1)

    def _tramp(self, uc, a, size, _):
        pc, saved, after = self.injections.pop("resume")
        if after:
            after(self)
        for r, v in saved.items():
            uc.reg_write(r, v)
        uc.reg_write(UC_ARM_REG_PC, pc)

    # ---- memory ----
    def cstr(self, addr, maxlen=600):
        b = bytes(self.uc.mem_read(addr, maxlen))
        return b[: b.index(0)].decode("latin1") if 0 in b else b.decode("latin1")

    def put(self, addr, data):
        self.uc.mem_write(addr, bytes(data))

    def alloc(self, n=256):
        a = self.heap
        self.heap += (n + 15) & ~15
        return a

    def u8(self, addr):
        return bytes(self.uc.mem_read(addr, 1))[0]

    def u16(self, addr):
        return struct.unpack("<H", self.uc.mem_read(addr, 2))[0]

    def u32(self, addr):
        return struct.unpack("<I", self.uc.mem_read(addr, 4))[0]

    def poke8(self, addr, v):
        self.uc.mem_write(addr, bytes([v & 0xFF]))

    def poke16(self, addr, v):
        self.uc.mem_write(addr, struct.pack("<H", v & 0xFFFF))

    def poke32(self, addr, v):
        self.uc.mem_write(addr, struct.pack("<I", v & 0xFFFFFFFF))

    def strp(self, s):
        a = self.alloc(len(s) + 1)
        self.put(a, s.encode() + b"\0")
        return a

    def names(self):
        return [t[0] for t in self.trace]

    def calls(self, name):
        return [t for t in self.trace if t[0] == name]

    def reset(self):
        self.trace = []
        self.violations = []

    def ring(self):
        """What logcap and v5_log have queued and not yet flushed."""
        return self.call("log_pending")

    # ---- GPIO MMIO, 16 pins per 0x20 bank: +0 input, +4 output ----
    def gpio(self, pin):
        return (self.u32(GPIO + (pin >> 4) * 0x20 + 4) >> (pin & 0xF)) & 1

    def gpio_input(self, pin, v):
        a = GPIO + (pin >> 4) * 0x20
        w = self.u32(a) & ~(1 << (pin & 0xF))
        self.poke32(a, w | ((v & 1) << (pin & 0xF)))

    # ---- stock models ----
    def _name(self, p):
        s = self.cstr(p).upper().replace("\\", "/")
        if s.startswith("G:/"):
            s = s[3:]
        return s.strip("/")

    def _card_live(self, what):
        if self.sd_up and self.mounted:
            return True
        self.violations.append(f"{what} while the card is {'unmounted' if self.sd_up else 'uninited (SD down)'}")
        return False

    # open 0x9e608(path, flags): fd >= 0, -1 on failure
    def f_open(self, m, path, flags, *a):
        name = self._name(path)
        if not self._card_live(f"open {name}"):
            return -1
        if flags in (O_READ_ONLY, O_READ_WRITE):
            if name not in self.card.f:
                return -1
        elif flags == O_CREATE_TRUNC:
            self.card.f[name] = bytearray()
        else:
            self.violations.append(f"open {name} with flags {flags:#x}")
            return -1
        fd = self.next_fd
        self.next_fd += 1
        self.fds[fd] = [name, 0]
        return fd

    # read 0x9e67c(fd, buf, n): bytes read, writes only those
    def f_read(self, m, fd, buf, n, *a):
        e = self.fds.get(fd)
        if not e:
            return -1
        data = bytes(self.card.f[e[0]][e[1] : e[1] + n])
        self.put(buf, data)
        e[1] += len(data)
        return len(data)

    def f_write(self, m, fd, buf, n, *a):
        e = self.fds.get(fd)
        if not e:
            return -1
        data = bytes(self.uc.mem_read(buf, n))
        f = self.card.f[e[0]]
        if e[1] + n > len(f):
            f.extend(b"\0" * (e[1] + n - len(f)))
        f[e[1] : e[1] + n] = data
        e[1] += n
        return n

    def f_lseek(self, m, fd, off, whence, *a):
        e = self.fds.get(fd)
        if not e:
            return -1
        if off >= 0x80000000:
            off -= 0x100000000
        base = {0: 0, 1: e[1], 2: len(self.card.f[e[0]])}[whence & 3]
        e[1] = max(0, base + off)
        return e[1]

    def f_close(self, m, fd, *a):
        return 0 if self.fds.pop(fd, None) else -1

    def f_unlink(self, m, path, *a):
        name = self._name(path)
        if not self._card_live(f"unlink {name}"):
            return -1
        if self.unlink_ret:
            return self.unlink_ret
        if name in self.card.f:
            del self.card.f[name]
            return 0
        return -1

    # stat 0x9e894(path, st): attr u16 at +0, size u64 at +8 with STRD (8-aligned
    # buffer or the store faults on ARMv5TE), time u32 at +0x10; 0 or -1
    def f_stat(self, m, path, st, *a):
        name = self._name(path)
        if not self._card_live(f"stat {name}"):
            return -1
        if name not in self.card.f:
            return -1
        if st & 7:
            self.violations.append(f"stat buffer {st:#x} not 8-aligned (STRD at 0x9e904)")
        self.put(st, struct.pack("<HHIQI", 0x20, 0, 0, len(self.card.f[name]), 0x4D8E0000))
        return 0

    # rename 0x9e6ec(old, new): 0 or -1
    def f_rename(self, m, old, new, *a):
        o, n = self._name(old), self._name(new)
        if not self._card_live(f"rename {o}"):
            return -1
        if o not in self.card.f or n in self.card.f:
            return -1
        self.card.f[n] = self.card.f.pop(o)
        return 0

    def f_flush(self, m, drive, *a):
        if drive == 6:
            self._card_live("flush")

    # mount 0x9df4c(drive): 0 or -1. With the SD driver down it brings the card up
    # through the SD0 drive's init op (table 0xe4978, op 0x9b570 -> SD init), as
    # stock's own mount retry (0x3dbc0, no SD init before it) relies on.
    def f_mount(self, m, drive, *a):
        if not self.sd_up:
            if self.sd_init_ret:
                return -1
            self.sd_up = True
        if self.mount_ret == 0:
            self.mounted = True
        return self.mount_ret

    # unmount 0x9df90(drive): -1 (0xffff sign-extended at 0x9dfb4) if a file is open
    def f_unmount(self, m, drive, *a):
        if self.fds or self.unmount_ret:
            return -1
        self.mounted = False
        return 0

    def f_sd_init(self, m, dev, *a):
        if self.sd_init_ret:
            return self.sd_init_ret
        self.sd_up = True
        return 0

    # SD driver uninit 0xb3844(dev): the card is down until an SD init or a mount
    def f_sd_uninit(self, m, dev, *a):
        self.sd_up = False
        self.mounted = False
        return 0

    def f_malloc(self, m, n, *a):
        a = self.heap
        self.heap += (max(n, 1) + 15) & ~15
        self.live[a] = n
        return a

    def f_free(self, m, p, *a):
        if p and self.live.pop(p, None) is None:
            self.violations.append(f"free of {p:#x}, not a live block")

    def f_delay(self, m, n, *a):
        self.ticks += n

    def f_rtc(self, m, out, *a):
        for i, v in enumerate(self.rtc):
            self.poke32(out + i * 4, v)
        return 0

    # msg post 0x42c(q, msg, item, size, [sp] urgent): 0 or -1 (0x4cc, 0x4ec)
    def f_msg_post(self, m, q, msg, item, size):
        sp = self.uc.reg_read(UC_ARM_REG_SP)
        urgent = self.u32(sp)
        words = struct.unpack("<2I", bytes(self.uc.mem_read(item, 8))) if item else (0, 0)
        self.trace[-1] = ("apq", q, msg, words[0], words[1], size, urgent)
        return 0

    # xQueueGenericSend 0x5d1b4(q, item, ticks, pos): 1 = pdPASS
    def f_qsend(self, m, q, item, ticks, pos):
        self.trace[-1] = ("qsend", q, self.u32(item), ticks, pos)
        return self.qsend_ret

    # msgQSend 0x5c320(q, item, ticks): 0 = sent (0x3e624 tests it); the trace keeps the first word
    def f_msgq_send(self, m, q, item, ticks, *a):
        self.trace[-1] = ("msgq_send", q, self.u32(item), ticks)
        return 0

    # SD LUN eject callback 0xbe2d4: SD driver uninit (0xb3844), 0
    def f_eject(self, m, lun, *a):
        self.sd_up = False
        self.mounted = False
        return 0

    # nv_read_page 0x594b8(flash_addr, dst): takes the NV mutex (portMAX_DELAY),
    # reads 256 B of NOR into dst, cleans and invalidates the D-cache over dst,
    # gives the mutex; returns spiflash_read's result, 0 = read OK (nv_read
    # 0x59528 retries on nonzero)
    def f_nv_read(self, m, addr, dst, *a):
        if not self.u32(W_NV_MUTEX):
            self.violations.append("nv_read_page before nv init created its mutex")
            return -1
        if addr in self.nv_fail:
            return -1
        self.put(dst, self.nor[addr : addr + 0x100])
        return 0


# Webcam EP0 (src/xu.S, docs/webcam.md). The USB device task (0x4d260 -> 0x4d220) runs the
# handlers usb_uvc_init installs (0x52a90): setup 0x507ac(1), IN done 0x4d150(0x15), OUT done
# 0x524b8(0x16). They run as they are; the device controller's EP0 primitives are modelled.
A_EP0_SETUP, A_EP0_IN_DONE, A_EP0_OUT_DONE = 0x507AC, 0x4D150, 0x524B8
A_SETUP_READ, A_EP0_WRITE, A_EP0_STALL, A_EP0_ACK = 0xBCC2C, 0xBC9CC, 0xBC99C, 0xBC9B4
A_EP0_ARM_OUT, A_EP0_OUT_COUNT, A_EP0_OUT_READ = 0xBCB18, 0xBCB5C, 0xBCB6C
EP0, UVC_ERR = 0x134E34, 0x12440A  # EP0 state (+0x28 data, +0x2c left, +0x30 setup); request error code


class Ep0:
    """Control transfers through the stock UVC EP0 handlers. Models: the setup read 0xbcc2c
    (8 B from the controller), the FIFO write 0xbc9cc (up to 64 B per call, read with word
    loads, so the data must be 4-aligned; returns the count), the stall 0xbc99c, arming the OUT
    data or status stage 0xbcb18, the OUT count 0xbcb5c and the FIFO read 0xbcb6c (refuses an
    unaligned buffer or more than 64 B, as the real one does)."""

    def __init__(self, m):
        # the stubs below are code hooks: a second Ep0 on the same Machine would add a second set
        assert not hasattr(m, "ep0"), "one Ep0 per Machine"
        m.ep0 = self
        self.m = m
        self.setup = bytes(8)
        self.out = b""
        self.ev = []
        m.stub(A_SETUP_READ, "ep0_setup", self._setup)
        m.stub(A_EP0_WRITE, "ep0_write", self._write)
        m.stub(A_EP0_STALL, "ep0_stall", lambda _m, *a: self.ev.append(("stall",)))
        m.stub(A_EP0_ACK, "ep0_ack", lambda _m, *a: None)
        m.stub(A_EP0_ARM_OUT, "ep0_arm", lambda _m, *a: self.ev.append(("arm",)))
        m.stub(A_EP0_OUT_COUNT, "ep0_count", lambda _m, *a: len(self.out))
        m.stub(A_EP0_OUT_READ, "ep0_read", self._read)

    def _setup(self, m, dst, *a):
        m.put(dst, self.setup)

    def _write(self, m, ptr, n, *a):
        n = min(n, 64)
        if n and ptr & 3:
            m.violations.append(f"EP0 FIFO write from {ptr:#x}: 0xbc9cc reads words, the data is not 4-aligned")
        self.ev.append(("in", bytes(m.uc.mem_read(ptr, n)) if n else b""))
        return n

    def _read(self, m, dst, n, *a):
        if dst & 3 or n > 64:
            m.violations.append(f"EP0 FIFO read of {n} B to {dst:#x}")
            return -1
        m.put(dst, self.out[:n])
        self.ev.append(("out", n))
        return 0

    def request(self, rt, req, value, index, length, data=b"", sp=STACK - 0x300):
        """One control transfer: the setup stage, then for an OUT request the stock handler
        armed, its data stage. Returns {stall, data (IN bytes), armed, status (the zero-length
        IN after OUT data), err (the request error code byte)}."""
        m = self.m
        self.ev = []
        self.setup = struct.pack("<BBHHH", rt, req, value, index, length)
        m.task("usb")
        m.call_arm(A_EP0_SETUP, 1, sp=sp)
        armed = ("arm",) in self.ev
        if armed and not rt & 0x80:
            self.out = bytes(data)
            m.call_arm(A_EP0_OUT_DONE, 0x16, sp=sp)
        ins = [e[1] for e in self.ev if e[0] == "in"]
        return {
            "stall": ("stall",) in self.ev,
            "data": b"".join(ins) if rt & 0x80 else None,
            "armed": armed,
            "status": not rt & 0x80 and ins == [b""],
            "err": m.u8(UVC_ERR),
        }

    def in_rest(self, sp=STACK - 0x300):
        """The IN-done handler 0x4d150 once per packet the host takes, until it arms the
        status stage; the bytes sent after the first packet."""
        m = self.m
        out = b""
        for _ in range(64):
            self.ev = []
            m.call_arm(A_EP0_IN_DONE, 0x15, sp=sp)
            out += b"".join(e[1] for e in self.ev if e[0] == "in")
            if ("arm",) in self.ev:
                return out
        raise EmuError("EP0 IN data stage did not end")

    def get(self, req, sel, unit, length, iface=0):
        return self.request(0xA1, req, sel << 8, unit << 8 | iface, length)

    def set_cur(self, sel, unit, data, length=None, iface=0, req=0x01):
        return self.request(0x21, req, sel << 8, unit << 8 | iface, len(data) if length is None else length, data)

    def error_code(self):
        """what Linux asks after a stall: GET_CUR of entity 0 selector 2 (0x51098), 1 B"""
        r = self.get(0x81, 2, 0, 1)
        return r["data"][0] if r["data"] and not r["stall"] else None


class Harness:
    def __init__(self, m):
        self.m = m
        self.fails = []
        self.checks = 0
        self.section = ""

    def ok(self, cond, what, detail=""):
        self.checks += 1
        if cond:
            print(f"  ok   {what}")
        else:
            self.fails.append(f"[{self.section}] {what} {detail}".strip())
            print(f"  FAIL {what} {detail}")
        return cond

    def eq(self, got, want, what):
        return self.ok(got == want, what, f"(got {got!r}, want {want!r})")

    def has(self, needle, what=None, m=None):
        blob = (m or self.m).card.get("LOG.TXT").decode("latin1")
        good = needle in blob
        return self.ok(good, what or f"LOG.TXT has {needle!r}", "" if good else f"\n    LOG.TXT: {blob[-600:]!r}")

    def called(self, name, what=None, m=None):
        return self.ok(name in (m or self.m).names(), what or f"{name} called")

    def not_called(self, name, what=None, m=None):
        return self.ok(name not in (m or self.m).names(), what or f"{name} not called")

    def clean(self, m, what="no model violations"):
        return self.ok(not m.violations, what, f"({m.violations})")

    def report(self):
        print(f"\n{self.checks - len(self.fails)}/{self.checks} checks passed")
        for f in self.fails:
            print(f"  FAIL {f}")
        return 1 if self.fails else 0
