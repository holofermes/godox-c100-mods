# Hardware

Tags: [read] = read in the firmware, [seen] = observed on a camera, [inferred] = deduction.
[seen, V1.1.3] = observed on a camera running firmware V1.1.3 or this patch's earlier build for
it; not repeated on V1.1.6.

| part | what |
| --- | --- |
| SoC | Generalplus GP22B, ARM926EJ-S class (ARMv5TE), little endian, FreeRTOS [read] |
| RAM | 16 MiB DRAM; the app runs from RAM 0 [read] |
| Flash | 2 MiB SPI NOR. The update file is the whole chip; 0x1C1800..0x200000 stays erased [seen, V1.1.3, NOR dump] |
| Sensor | GalaxyCore GC2083, 1920x1080 RAW Bayer: chip ID 0x2083 read over SCCB on a camera (GHDK). The boot log's `Sensor GC4653 30fps init completed` comes from the GC2083 driver's own init (0x4c8f0), a mislabel in the SDK [read] |
| Screen | transparent segment LCD on an HT1621 [read; seen, V1.1.3] |
| Radio | none: no FCC ID, no antenna in teardown photos. The SDK's WiFi code and config are orphaned [read] |
| USB | USB-C, device only |

## Pins and inputs

| GPIO | role |
| --- | --- |
| 40, 41, 42 | HT1621 CS, WR, DATA (bit-banged) [read] |
| 43 | green status LED, lit at 1: on while idle, dark while the shutter is held, blinks while recording [seen, V1.1.3] |
| 95 | the SDK's LED/backlight line, read back by the USB plug-in path (0x1f5b4); nothing visible [inferred] |
| 70 | VBUS sense, polled by the VBUS state machine 0x1f3d4 [read] |
| 71, 38, 39 | mode switch Off / Photo / Video / Metering, decoded at 0x1e658 [read] |

The red light is the charger chip, lit while charging, also with the camera off [seen, V1.1.3].

Buttons are ADC keys; each handler runs in the peripheral task and posts to the app queue:

| button | handler | log line |
| --- | --- | --- |
| shutter | `ap_peripheral_ok_key_exe` 0x1df64 | `adc_key_judge -Key 1` [seen, V1.1.3] |
| middle | `ap_peripheral_F_key_exe` 0x1dd6c | `adc_key_judge -Key 2` [seen, V1.1.3] |
| right | `ap_peripheral_T_key_exe` 0x1de20 | `adc_key_judge -Key 3` [seen, V1.1.3] |
| left | `ap_peripheral_IO_ISO_key_exe` 0x1dcb8 | [seen, V1.1.3] |
| power (long) | `ap_peripheral_pw_key_exe` 0x1e130 | [read] |

Left and right change the aspect ratio in Photo and Video (docs/webcam.md).

The reset hole next to the USB-C port is a hardware reset; it does not reflash.

## Screen

HT1621, 25 SEG x 3 COM = 75 segments. `seg_refresh` 0x21318 redraws it from a 25-byte shadow
(0x1240e0) every ~250 ms, from the peripheral task's 0x6001 tick. Boot sends SYS EN 0x01,
RC256K 0x18, BIAS 0x24, LCD ON 0x03; the patch uses LCD OFF 0x02 and LCD ON 0x03 [read; seen,
V1.1.3]. There is no lock around the bus, so anything else that talks to it has to run in the
peripheral task.

The image also carries the SDK's raster stack for an AUO A027DTN01.9 320x240 TFT, and
`task_display_entry` runs both inits at boot. The camera shows a segment LCD, so no TFT is
fitted [inferred].

## Debug access

- UART1 console (`task_uart_cmd_entry`), pads not located. The firmware prints a lot to it;
  the patch sends those prints to `LOG.TXT` instead, when `log=on`.
- JTAG: the firmware never disables it [read]. Untested; needs the case open.
