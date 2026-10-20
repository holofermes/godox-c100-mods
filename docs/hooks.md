# How the patch works

Tags: [read] = read in the firmware, [seen] = observed on a camera, [inferred] = deduction.

`tools/base.py` turns stock into the base image (webcam on plug-in, auto-off held on USB
power). `tools/patch.py` then links the C payload from `src/` into the free RAM gap and patches
branches into stock code that jump to it. Every patch site asserts its original bytes.

## Payload region

RAM 0xE6828..0xE8000, 6104 B, between the end of stock .data and the start of .bss
(docs/firmware.md). `tools/patch.py` raises the load length at file 0x1002E from 0x736 to 0x741
so the boot block copies it. Code, data and .bss of the payload all live there; crt0 never
touches it. With GCC 14.2 the payload is 5784 B of code and data plus 117 B of .bss, 203 B spare.

C is Thumb (`-mthumb -Os`); the entry stubs (`src/stubs.S`, `src/v6.S`) are ARM, as the stock
code is. The ones in `src/stubs.S` that call C do it through `blx`; the webcam gates in `src/v6.S`
call none.

## The arm gate

Until armed, every stub except SET runs only the stock instruction it replaced and enters no C (the
sound stub also holds back the power-on chime). SET always calls `load_settings`, which is what
arms. G6 is a pointer, not a stub: `v5_eject_cb` runs armed or not, and unarmed it only calls the
stock callback. Armed means both:

- `st.booted`, set at the end of `load_settings`, which runs from the SET hook at the end of
  the VERSION.TXT writer on every card mount, after the update check;
- `[0x123cfc] == 1`, stock .bss: 0 at boot, 2 once the updater starts, 1 after a first mount
  that found no update.

So none of the patch's C runs before the card updater or during a flash, and a broken feature
cannot block recovery. Without a card nothing arms and the camera runs as the base image.

## Hooks

Ids: D the display-refresh tick, G USB and VBUS, K keys, L the LED pins, S sound, V6 the webcam
gates and 1080p data; LOG, SET and STACK are named for what they patch. There is no G5.

| id | RAM | stock | replaced by | runs |
| --- | --- | --- | --- | --- |
| LOG | 0x535e0 | in printf, after formatting | `logcap_stub`: send the line to the log ring in place of the UART | any task, from boot |
| SET | 0x37df4 | end of the VERSION.TXT writer | `settings_stub`: read `C100.TXT`, arm | each mount, after the update check |
| D1 | 0x22238 | `bl seg_refresh` in the 0x6001 tick | `tick_stub`: refresh, then `v5_tick` | peripheral task, ~4 Hz |
| G1 | 0x1f540 | VBUS insert, state 0 | `plug_stub`: pick the USB personality | peripheral task |
| G2 | 0x1f4c0 | VBUS remove, state 1 | `unplug_stub` | peripheral task |
| G3a, G3b | 0x22138, 0x22354 | VBUS poll gate and poll | poll every tick once armed | peripheral task |
| K1 | 0x1dda4 | middle button held | `long_select_stub`: display/LED toggle | peripheral task |
| L1 | 0x43224 | gpio_set entry | `v5_gpio_set`: hold pins 43/95 at the off level | any task, from boot |
| L2, L3 | 0x1f544, 0x1f5ec | GPIO 95 read-backs | `v5_get95` | peripheral task |
| S1 | 0x22958 | play_effect entry | `v5_sound_gate` | any task, from boot |
| G6 | 0xe0848 | MSC eject callback pointer | `v5_eject_cb` | USB task |
| G4 | file 0x36664 | PC-disconnect pool word | 0x8002 drive uninit (the base image has 0x8005) | |
| G7 | 0x21f58.. | auto-off hold in VBUS state 1 (base image; stock: state 2) | states 1 and 2 | peripheral task |
| STACK | file 0xeaafc | peripheral task stack 0x800 | 0x1000 | boot |
| V6A | 0x2ea90, 0x36f4c, 0x36f7c | `bl 0x21318` aspect step | `v6_aspect_gate` (docs/webcam.md) | key press |
| V6R | 0x356e0 | `beq` record start | `v6_rec_gate` | record start |
| V6D | 0xf0e4, 0xf0f0, file 0xf3b0a, 0xf3b16 | webcam size | 1920x1080 | stream start |

The peripheral task stack goes to 4 KB because the patch's card writes run in that task and a static
bound with stock FAT and SD frames reached 3.1 KB on SD error paths. The stack size is read
only by InitTask's copy of the task table (literal at 0x5f058), and pvPortMalloc serves a
4 KB request from its pool or the heap [read].

## The canary

`CANARY=1 ./build.sh STOCK.BIN` keeps every boot-time byte (load length, the boot-time hooks with
their gate, the stack size) and replaces the C with one line written to `LOG.TXT` on each mount;
it never arms. It leaves out the webcam gates, the 1080p data and G4, none of which acts at boot.
It was flashed on a camera, booted and logged, flashed stock from itself, then itself again
[seen]. The full build's 194 stub words match the canary's except the 10 literal words holding
addresses of C symbols, so that run covers the stub code the full build ships. Redo it after any
change to `src/stubs.S`, the load length or the stack size.

## Tests

`tests/` runs the built image under Unicorn: the stock code around each hook, the C payload,
and models of the file API, heap, queues, GPIO, HT1621 and the card. 350 checks for the full
build, 83 for the canary. They do not model the scheduler, IRQs, the MMU and caches, real SD or
USB behaviour, or failures of malloc, read, write or the RTC.

    uv run --group test python tests/test_v5.py build/GP_CARDVR_UPGRADE.BIN obj/v5.sym
    CANARY=1 ./build.sh STOCK.BIN
    uv run --group test python tests/test_canary.py build-canary/GP_CARDVR_UPGRADE.BIN obj-canary/v5.sym

## Known gaps

- After an eject, the remount skips stock's SD pre-init and retry loop; if `sd_init` fails,
  the patch retries 6 times and then waits for stock's next mount.
- `play_effect` for the late power-on chime runs from the peripheral task, and the RTC read for
  log headers takes no lock; both share state with other tasks.
- Each log flush copies up to 8 KB with IRQs masked.
