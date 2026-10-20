# Firmware

Tags: [read] = read in the firmware, [seen] = observed on a camera, [inferred] = deduction.
[seen, V1.1.3] = observed on a camera running firmware V1.1.3 or this patch's earlier build for
it; not repeated on V1.1.6.

Stock: V1.1.6, [Godox_C100_V1.1.6.zip](https://www.godox.com/Downloads/Godox_C100_V1.1.6.zip)
from godox.com (sha256 `3fc9b15c03f89fbe1a0d6f23d081ec1b312cdedd42a99115a6f47d639d9598d1`).
Inside it, `c100_v1.1.6_20260929/GP_CARDVR_UPGRADE.BIN`, 1,841,152 B (0x1C1800), sha256
`411a553fc58810a1ea5b04ef46819deea317d574246ef5870c31d79d68f95036`. Generalplus card-DVR SDK,
FreeRTOS, ARM32. Addresses in these docs are app RAM addresses unless marked file:
**file = RAM + 0x10200**.

## Layout

| file | size | contents |
| --- | --- | --- |
| 0x000000 | 0x200 | SPI BootHeader, magic `PGpssiipps` |
| 0x000200 | 0xFE00 | boot block: vectors, crt0, SPI NOR driver; runs at `file + 0xF8027E20` |
| 0x010000 | 0x20 | section header `GP` ... `GPCODEROM`; u32 at 0x1002E = load length in 512 B sectors |
| 0x010200 | 0xE6968 | the app, copied to RAM 0: code, rodata, .data (ends RAM 0xE6968) |
| 0x0F6B68 | 0x79498 | zero padding; the patch stores its overlay at 0x100000 |
| 0x170000 | 0x51800 | resource directory and resources (sounds, images, font, UI pack) |

Resources: a directory of `{ char name[20]; u32 sector; }` entries at 0x170000, payload at
`0x170000 + sector * 0x200`. Images are `GPZP`: raw DEFLATE (fixed Huffman only, the on-device
inflater has no dynamic-block path) + crc32 + size, RGB565 inside. mullwar's tool unpacks and
repacks them.

The app reads flash only through `nv_read_page` 0x594b8 (256 B per call, under a mutex); its
callers outside the updater all read at 0x170000 and above [read].

## Boot

1. The mask ROM reads the BootHeader and loads the boot block to SRAM.
2. The boot block copies (N-1) x 512 B of the app to RAM 0, N = the u32 at file 0x1002E
   (stock 0x736). N is not checked.
3. The app's crt0 zeroes .bss, RAM 0xE8000..0x202A70, sets up stacks and calls main 0x5f290.
4. main sets up a small-block pool at 0x208AC0..0x24AAC0 and the FreeRTOS heap from 0x24AAC0.
   The heap's size is one literal, at 0x5f46c: the region ends at that literal + 0x42000
   (0xFFFFFF in stock); heap_5 puts its end marker at 0xFFFF80 (14,374,080 B). Then the MMU
   (0x5f920) and the caches: RAM is mapped 1:1 in 1 MiB sections, write-back cached, with an
   uncached alias at 0x80000000 [read].

RAM 0xE6968..0xE8000 (5,784 B) is free: after .data, before .bss. With N = 0x741 the boot block
copies the file bytes for it, which is where the patch's boot-time code lives (docs/hooks.md). A
bigger N buys nothing, since crt0 zeroes everything from 0xE8000 up.

No address in the app's code or data points at 0xFF0000..0x1000000, or at its uncached alias;
the constants in that range are colours, masks, SD command arguments, table records and message
values [read]. The patch lowers the heap literal by 64 KB, so the heap ends at 0xFEFF80 and that
range is free for the overlay (docs/hooks.md).

## Card updater

Part of the app, not the boot block [read]:

1. Every full card mount looks for `G:\GP_CARDVR_UPGRADE.BIN` (0x25968, called at 0x3df94),
   but only the first mount call of a power-on can start the update: the storage task sets
   [0x123d34] to 1 after that call whatever its result (0x3e574..0x3e580), and the mount takes
   the update branch only while the byte is 0 (0x3dfa0). So if the first mount fails, that
   power-on will not flash; switch off and on again with the card in.
2. Size check: 1,836,000 to 1,846,000 B, else it returns before touching flash (0x253c8).
3. It reads the whole file into RAM. Then it checks that the file starts with `PGps` (the
   BootHeader every image has) and gives up before anything is erased if not.
4. It deletes every task except itself, `Tmr Svc`, `IDLE` and the UART task, then erases 64 KiB
   blocks from flash 0 upward, programs 256 B pages and compares each page with the RAM copy, up
   to 20 retries per block.
5. No signature, no checksum over the file, no model or version check.
6. It deletes the file and restarts the camera (0x1fbac: RTC alarm and watchdog) [seen, V1.1.3].
   The clock resets. A file with the FAT read-only bit is kept and the camera powers off instead.

A full NOR dump read back from a running camera matched the flashed image byte for byte, and
the rest of the chip was erased [seen, V1.1.3, one dump]. The firmware has a settings writer
(0x59bc0) that erases and programs flash above the resources, from 0x170000 plus the resource
area rounded up [read]; it never touches 0x100000..0x170000. The updater writes the file to
flash byte for byte [read]; the patch relies on it to find its overlay in flash at the file
offset.

## Recovery limits

- The updater needs the app to reach its first card mount. A patch that crashes before that,
  on every boot, cannot be undone from the card. The boot block has no SD, USB or key code.
- Power loss while flashing blocks 0 to 15 (boot block and app) leaves a camera that cannot
  boot [inferred]; there is no A/B copy. Flash with a charged battery. The overlay sits in
  block 16: losing that one leaves the app intact and the overlay fails its CRC [inferred].
- A flash that fails all 20 retries on a block still deletes the file and restarts into the
  half-written flash [read].
- What the mask ROM does with a missing or bad BootHeader is unknown.
- JTAG is not disabled by the firmware [read]; untested, needs the case open.

The patch is built around the first point: its features load and switch on only after the update
check (docs/hooks.md). The boot-time part was proven on a camera with the canary for the V1.1.3
build [seen, V1.1.3]; the V1.1.6 canary is not yet run.

## Useful stock functions

A few; `src/fw.h` has prototypes for these and more:

| RAM | what |
| --- | --- |
| 0x53758 | printf (to UART1; the patch hooks it at 0x537b4) |
| 0x5374c, 0x53754 | malloc, free (pvPortMalloc, NULL on failure) |
| 0x9e608, 0x9e67c, 0x9e9c8, 0x9dc7c | open, read, write, close |
| 0x9e96c, 0x9e894, 0x9ac6c | unlink, stat, flush drive (6 = G:) |
| 0x433f4, 0x433b8 | gpio_set, gpio_read |
| 0x594b8 | read 256 B of SPI NOR (nv_read_page; spiflash_read 0x469d4 underneath) |
| 0x40158 | clean a D-cache range |
| 0x42c | post to an app queue |
| 0x1fbac | restart through the RTC alarm |

The fs mutex these take is a plain FreeRTOS mutex, not recursive.
