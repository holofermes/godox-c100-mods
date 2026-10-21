# How the patch works

Tags: [read] = read in the firmware, [seen] = observed on a camera, [inferred] = deduction.

`tools/base.py` turns stock into the base image (webcam on plug-in, no auto power-off on USB
power). `tools/patch.py` then puts the patch's code in two places and patches branches into
stock code that jump to it. Every patch site asserts its original bytes.

## Two places for the code

The stock image leaves 5,784 B of RAM unused, too little for all of it. So the code is split:

- **Gap**, RAM 0xE6968..0xE8000, between the end of stock .data and the start of .bss
  (docs/firmware.md). The boot block copies it from the image with the rest of the app once
  `tools/patch.py` raises the load length at file 0x1002E from 0x736 to 0x741; crt0 never touches
  it. It holds only what runs before the features are switched on: the ARM entry stubs
  (`src/stubs.S`), the overlay loader and the state the stubs read (`src/loader.c`), and, linked
  last, the webcam fixes and the webcam's config descriptor (`src/v6.S`), then the extension
  unit's EP0 code and mailbox (`src/xu.S`). 2,372 B of it are used.
- **Overlay**, RAM 0xFF0000..0x1000000, the top 64 KB of the 16 MiB of RAM. Stock gives this to
  the FreeRTOS heap; the patch lowers the heap-size literal at 0x5f46c from 0xFBDFFF to 0xFADFFF,
  so the heap ends at 0xFEFF80 instead of 0xFFFF80; no stock code or data refers to the range
  [read]. All the feature code, with its data and .bss, is linked there. Its bytes are stored in
  the update file's zero padding at file 0x100000, past what the boot block copies. The updater
  writes the file to flash byte for byte, so that is also where they are in flash. With the 64 KB
  taken from the heap, the free heap stayed at 10,963,456 B while a PC streamed [seen].

C is Thumb (`-mthumb -Os`); the stubs and webcam fixes are ARM, as the stock code is. Calls between
the gap and the overlay go through a register: the two are 15.7 MB apart, beyond a Thumb BL.

## Loading the overlay

On a card mount, the SET hook calls `v5_set` (gap). On the first mount of each power-on, or later
if no load has worked yet, it loads the overlay:

1. Return at once if `[0x123d34] == 2`: the updater started on this boot, and a card
   re-inserted during the update window reaches SET too [read].
2. Check that the firmware's flash reader is up: its mutex and its resource directory, both set
   by nv init in the state task's start-up code [read].
3. Read the first 256 B page from flash 0x100000 to 0xFF0000 with `nv_read_page` 0x594b8, the
   function stock reads its sounds and images with. It takes a mutex around each page, so it
   cannot collide with those reads [read].
4. Check the 16 B header there: magic `COVL`, then the body length and CRC-32 against the two
   words `tools/patch.py` wrote into the gap from the same build's overlay. A gap from one build
   never runs another build's overlay, for example after a flash cut between erase blocks 15
   (app) and 16 (overlay).
5. Read the remaining pages, check the body against the CRC, zero the overlay's .bss.
6. Clean the D-cache over the whole overlay (stock routine 0x40158), then invalidate the I-cache
   (`mcr p15, 0, r0, c7, c5, 0`, which the firmware has no routine for). The firmware runs
   with both caches on, and the CPU fetches code through the I-cache, which does not see what
   is still in the D-cache [read].
7. Call the overlay's `load_settings`.

On the camera the overlay loads on every boot; with `log=on` each boot logs
`[v5] v5 G+O B, C100.TXT ok`, the bytes used in the gap and in the overlay (1348+5220 on the
03023f67 build) [seen].

Any failure (reader not up, a page read error, wrong magic, another build's length or CRC, a
body that does not match its CRC) returns before step 7 and appends `ovl err N` to `LOG.TXT`,
whatever the log setting: 1 reader not up, 2 read error, 3 magic, 4 length, 5 body CRC, 6
another build's overlay. It uses the stock file calls, which take the fs mutex themselves; the
storage task holds no fs lock at SET [read]. Nothing in the overlay runs and the camera behaves
as the base image: webcam on USB, no auto-off on USB power, sounds as stock except the startup
chime, which stays silent. The next fresh mount (a card re-insert, or the next power-on) tries
again; the storage task's periodic re-checks with the card still in do not reach SET [read].
These paths are checked in the emulator only.

The load takes 21 page reads for the current overlay. The SET stub and the loader use 48 B of
stack, inside the 0x58 B frame the VERSION.TXT writer has just released (emulator);
`nv_read_page` and what it calls use about 300 B more [inferred from their frames].

## When the features switch on

Until the camera has read the card and checked it for a firmware update, every hook except SET runs
only the stock instruction it replaced, and none of the patch's C runs. The sound hook also catches
sound ids 2 and 3 and does not play them: in stock only the startup chime plays id 2 (0x35124) and
nothing plays id 3, while the over-temperature warning (message 0x7017) plays id 0 through 0x27a30
and is let through [read]. A caught id is played once the features switch on, if sound is on and
the camera has been on for under 15 s. SET always calls `v5_set`, the loader; without it the
features never switch on. They are on when both are true:

- `st.booted`, set at the end of the overlay's `load_settings`. `v5_set` clears it before every
  load and calls `load_settings` only after a good load, so it means "the overlay was loaded
  and checked on this power-on";
- `[0x123d34] == 1`, stock .bss: 0 at boot, 2 once the updater starts, 1 after the first
  mount call that did not start it, whatever that mount's result [read].

SET runs at the end of the VERSION.TXT writer on every fresh card mount, after the update check,
and not on the mount that starts an update [read]; a card re-inserted during the update finds
`[0x123d34] == 2` and `v5_set` returns. So none of the overlay runs before the card updater,
during a flash, or after a failed load, and a broken feature cannot block recovery. Without a
card the features never switch on and the camera runs as the base image.

The eject callback (G6) is a pointer the USB task calls whether or not the features are on, so it
points at a stub in the gap: before the features are on it goes straight to the stock callback,
after that to the overlay's `v5_eject_cb`.

## Hooks

Ids: D the display-refresh tick, G USB and VBUS, K keys, L the LED pins, S sound, V6 the webcam
fixes, sizes and descriptor, XU the extension unit; LOG, SET, STACK and HEAP are named for what
they patch. There is no G5.

| id | RAM | stock | replaced by | runs |
| --- | --- | --- | --- | --- |
| LOG | 0x537b4 | in printf, after formatting | `logcap_stub`: send the line to the log ring in place of the UART | any task, from boot |
| SET | 0x37fa0 | end of the VERSION.TXT writer | `settings_stub`: load the overlay, read `C100.TXT`, switch the features on | each mount, after the update check |
| D1 | 0x22360 | `bl seg_refresh` in the 0x6001 tick | `tick_stub`: refresh, then `v5_tick` | peripheral task, ~4 Hz |
| G1 | 0x1f5b0 | VBUS insert, state 0 | `plug_stub`: pick the USB personality | peripheral task |
| G2 | 0x1f530 | VBUS remove, state 1 | `unplug_stub` | peripheral task |
| G3a, G3b | 0x22260, 0x2247c | VBUS poll condition and poll | poll every tick once the features are on | peripheral task |
| K1 | 0x1dda4 | middle button held | `long_select_stub`: display/LED toggle | peripheral task |
| L1 | 0x433f4 | gpio_set entry | `v5_gpio_set`: hold pins 43/95 at the off level | any task, from boot |
| L2, L3 | 0x1f5b4, 0x1f65c | GPIO 95 read-backs | `v5_get95` | peripheral task |
| S1 | 0x22a80 | play_effect entry | `v5_sound_gate` | any task, from boot |
| G6 | 0xe0978 | MSC eject callback pointer | `eject_stub`, then `v5_eject_cb` once the features are on | USB task |
| G4 | file 0x3680c | PC-disconnect pool word | 0x8002 drive uninit (the base image has 0x8005) | |
| G7 | 0x22080.. | no auto power-off in VBUS state 1 (base image; stock: state 2) | states 1 and 2 | peripheral task |
| STACK | file 0xeac2c | peripheral task stack 0x800 | 0x1000 | boot |
| HEAP | 0x5f46c | heap-size literal 0xFBDFFF | 0xFADFFF: keeps 0xFF0000..0x1000000 for the overlay | boot, in main |
| V6A | 0x2ec48, 0x37104, 0x37134 | `bl 0x213e0` aspect step | `v6_aspect_gate` (docs/webcam.md) | key press |
| V6R | 0x35898 | `beq` record start | `v6_rec_gate` | record start |
| V6D | 0xf0e4, 0xf0f0 | APMode 0x106 target 1280x720 | 1920x1080 | stream start |
| V6S | 0xf510 | 0x106 second path = target | `v6_size`: the host's frame size, zoom 100 | stream start |
| V6S | 0xf514, 0xf4e0, 0xf4e4 | second ldrh; 0x106 display = path 0 | nop; display = second path | stream start |
| V6P | 0x527d4, 0x527d8 | probe: bFrameIndex 1 only | 0..6 | host SET_CUR |
| V6C | pools 0x3ef00, 0x52488 | config descriptor 0xe39d0 | `v6_cfg`, `v6_cfg` + 0xd8 (docs/webcam.md) | webcam plug-in |
| XU | 0x50980, 0x507f0 | class GET and SET setup, `ldrh`/`ldrb` of wIndex | `xu_in`, `xu_out`: unit 6's requests, else stock | host request, webcam mode |
| XU | 0x52570 | SET_CUR data stage, `cmp r6, #1` | `xu_data`: the line into the mailbox, else stock | host request, webcam mode |

The peripheral task stack goes to 4 KB because the patch's card writes run in that task and a
static bound with stock FAT and SD frames comes to at most 3.1 KB, on SD error paths. The stack
size is read only by InitTask's copy of the task table (literal at 0x5f22c), and pvPortMalloc
serves a 4 KB request from its pool or the heap [read].

The low-battery power-off only runs while no USB power is present, so on USB power it never turns
the camera off [read].

## The canary

`CANARY=1 ./build.sh STOCK.BIN` keeps every boot-time byte (load length, the boot-time hooks with
their on/off check, the stack size, the heap literal, the loader) and replaces the overlay with one
function: on each mount it appends `canary 2 ovl <crc> chime <n>` to `LOG.TXT`, where `<crc>` is
the overlay CRC the loader checked (build.sh prints it) and `<n>` is the sound id the sound hook
caught (2 when it caught the startup chime). It never switches the features on. It leaves out the
webcam fixes, descriptor and extension unit (`src/v6.S`, `src/xu.S`, the V6 and XU rows) and G4.

The full build's gap matches the canary's 321 words of stubs, loader, literal pools and loader data
except 14 words: 12 literal words, each the address of something in the overlay (the C entry
points, `led`, the overlay's bounds and entry), and the two words of the overlay's length and CRC.
Every gap symbol, `st` included, is at the same address. So a canary run on the camera covers the
stub and loader code the full build ships, the heap reservation, the flash read, the cache
maintenance and the call into the overlay. It does not cover `src/v6.S` and `src/xu.S`, which are
linked after all of that, so they move nothing the canary proved. The webcam fixes run on
left/right, record start and stream start without waiting for the features to switch on, so they
can run before the first mount; they read only stock bytes (0x123d3c, 0xe3b2f), `v6_size` writes
only the zoom bytes, and they branch to stock code. The extension unit's code runs only on a host
request in webcam mode; it reads the request, [0x123d34], `st.booted` and its mailbox, writes only
the mailbox and stock's error byte 0x12440a, calls nothing and branches back to stock code. They
were checked in the emulator and the disassembly only. Redo the canary after any change to
`src/stubs.S`, `src/loader.c`, the load length, the stack size or the heap literal. Check changes
to `src/v6.S` and `src/xu.S` in the emulator.

The canary was flashed on a camera, booted and logged, flashed stock from itself, then was
flashed again from stock [seen].

## Tests

`tests/` runs the built image under Unicorn as an ARM926: the stock code around each hook, the
loader, the overlay's C, and models of the file API, heap, queues, GPIO, HT1621, the card and the
flash (the image file itself). They check the heap setup with the patched literal by running
stock's own heap code, the loader's success path (pages read, header, CRC, .bss zeroed, D-cache
clean over the overlay, the I-cache invalidate, then the entry), that a failed flash read,
reader not up, bad magic, bad length, bad CRC or a self-consistent overlay from another build
each leave the features off, run no overlay code and log `ovl err N`, and that a mount during an
update loads nothing. For the webcam: the descriptor walk and the stock USB code that serves it,
the probe per frame index, the APMode 0x106 geometry per index, and `webcam` and `drive` from the
command to the boot after, both markers at once. For the extension unit: its place on the chain,
the whole descriptor read through the stock EP0 handlers (the device controller modelled), each
of its requests and refusals, 1,875 other class requests answered as on the base image, and every
command taken from the mailbox, while streaming too. 778 checks for the full build, 144 for the
canary. They do not model the scheduler, IRQs, the MMU and caches (the cache instructions run as
no-ops; the calls are checked), real SD, flash or USB behaviour, or failures of malloc, read,
write or the RTC.

    uv run --group test python tests/test_v5.py build/GP_CARDVR_UPGRADE.BIN obj/v5.sym
    CANARY=1 ./build.sh STOCK.BIN
    uv run --group test python tests/test_canary.py build-canary/GP_CARDVR_UPGRADE.BIN obj-canary/v5.sym

## Known gaps

- After an eject, the remount makes one attempt of stock's mount (fs lock, SD init, mount) per
  try and, like stock, runs SD uninit after a failed SD init, or unmount and SD uninit after a
  failed mount, then retries the mount alone. It waits 5 s between tries where stock waits
  100 ms, stops after 6 and then waits for stock's next mount.
- `play_effect` for the late power-on chime runs from the peripheral task, and the RTC read for
  log headers takes no lock; both share state with other tasks.
- Each log flush copies up to 8 KB with IRQs masked.
