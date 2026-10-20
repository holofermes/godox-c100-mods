# Firmware

Tags: [read] = read in the firmware, [seen] = observed on a camera, [inferred] = deduction.

Stock: `Godox_Firmware_C100_V1.1.3.zip` from godox.com. `GP_CARDVR_UPGRADE.BIN`, 1,841,152 B
(0x1C1800), sha256 `f86b8c1c47b3ee29c494d38ecd5a5300cf36b2156a29d5aecb11f8a73ce922a3`.
Generalplus card-DVR SDK, FreeRTOS, ARM32. Addresses in these docs are app RAM addresses
unless marked file: **file = RAM + 0x10200**.

## Layout

| file | size | contents |
| --- | --- | --- |
| 0x000000 | 0x200 | SPI BootHeader, magic `PGpssiipps` |
| 0x000200 | 0xFE00 | boot block: vectors, crt0, SPI NOR driver; runs at `file + 0xF8027E20` |
| 0x010000 | 0x20 | section header `GP` ... `GPCODEROM`; u32 at 0x1002E = load length in 512 B sectors |
| 0x010200 | 0xE6828 | the app, copied to RAM 0: code, rodata, .data (ends RAM 0xE6828) |
| 0x0F6A28 | 0x795D8 | zero padding |
| 0x170000 | 0x51800 | resource directory and resources (sounds, images, font, UI pack) |

Resources: a directory of `{ char name[20]; u32 sector; }` entries at 0x170000, payload at
`0x170000 + sector * 0x200`. Images are `GPZP`: raw DEFLATE (fixed Huffman only, the on-device
inflater has no dynamic-block path) + crc32 + size, RGB565 inside. mullwar's tool unpacks and
repacks them.

## Boot

1. The mask ROM reads the BootHeader and loads the boot block to SRAM.
2. The boot block copies (N-1) x 512 B of the app to RAM 0, N = the u32 at file 0x1002E
   (stock 0x736). N is not checked.
3. The app's crt0 zeroes .bss, RAM 0xE8000..0x202A70, sets up stacks and starts FreeRTOS.
   The FreeRTOS heap is 0x24AAC0..0xFFFF80, about 14 MB free at boot.

RAM 0xE6828..0xE8000 (6104 B) is free: after .data, before .bss. With N = 0x741 the boot block
copies the file bytes for it, which is where the patch's code lives (docs/hooks.md). A bigger N
buys nothing, since crt0 zeroes everything from 0xE8000 up.

## Card updater

Part of the app, not the boot block [read]:

1. On the first card mount after power-on, the storage task opens `G:\GP_CARDVR_UPGRADE.BIN`
   (0x257c0). Later mounts in the same power-on do not.
2. Size gate: 1,836,000 to 1,846,000 B, else it returns before touching flash (0x252b0).
3. It reads the whole file into RAM, deletes every task except itself, `Tmr Svc`, `IDLE` and
   the UART task, then erases 64 KiB blocks from flash 0 upward, programs 256 B pages and
   compares each page with the RAM copy, up to 20 retries per block.
4. No signature, no checksum over the file, no model or version check.
5. It deletes the file and restarts the camera (0x1fb3c: RTC alarm and watchdog) [seen]. The
   clock resets. A file with the FAT read-only bit is kept and the camera powers off instead.

A full NOR dump read back from a running camera matched the flashed image byte for byte [seen,
one dump], so the firmware does not write NOR at runtime and settings are not stored in flash
[inferred].

## Recovery limits

- The updater needs the app to reach its first card mount. A patch that crashes before that,
  on every boot, cannot be undone from the card. The boot block has no SD, USB or key code.
- Power loss while flashing blocks 0 to 15 (boot block and app) leaves a camera that cannot
  boot [inferred]; there is no A/B copy. Flash with a charged battery.
- A flash that fails all 20 retries on a block still deletes the file and restarts into the
  half-written flash [read].
- What the mask ROM does with a missing or bad BootHeader is unknown.
- JTAG is not disabled by the firmware [read]; untested, needs the case open.

The patch is built around the first point: its C code stays switched off until after the update
check (docs/hooks.md), and the boot-time part was proven on a camera with the canary.

## Useful stock functions

A few; `src/fw.h` has prototypes for these, except the NOR read, and more:

| RAM | what |
| --- | --- |
| 0x53584 | printf (to UART1; the patch hooks it at 0x535e0) |
| 0x53578, 0x53580 | malloc, free (pvPortMalloc, NULL on failure) |
| 0x9e434, 0x9e4a8, 0x9e7f4, 0x9daa8 | open, read, write, close |
| 0x9e798, 0x9e6c0, 0x9aa98 | unlink, stat, flush drive (6 = G:) |
| 0x43224, 0x431e8 | gpio_set, gpio_read |
| 0x46804 | SPI NOR read |
| 0x42c | post to an app queue |
| 0x1fb3c | restart through the RTC alarm |

The fs mutex these take is a plain FreeRTOS mutex, not recursive.
