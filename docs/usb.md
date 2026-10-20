# USB

Tags: [read] = read in the firmware, [seen] = observed on a camera, [inferred] = deduction.
[seen, V1.1.3] = observed on a camera running firmware V1.1.3 or this patch's earlier build for
it; not repeated on V1.1.6. The USB code read here is the same in V1.1.6, moved [read], but the
V1.1.6 build is not yet tested on a camera.

## Personalities

The USB task (queue at [0x123d6c]) takes these messages [read]:

| msg | does |
| --- | --- |
| 0x8001 | drive init (`usb_msdc_init` 0x3e954): the card as a USB drive, `1b3f:8301` |
| 0x8002 | drive uninit, including the SD driver uninit 0xb3844 |
| 0x8003 | webcam init (`usb_uvc_init` 0x3ed7c): UVC + UAC, `1b3f:2002` |
| 0x8005 | webcam uninit |
| 0x8006 | charger unplugged |
| 0x8007 | stream start (alt setting > 0); the base patch makes it run the encoder in APMode 0x106 |
| 0x8008 | stream stop |

Stock posts 0x8001 on every plug-in, so the webcam is fully linked but never selected. The base
patch changes the plug-in word to 0x8003. The patch posts per switch position from `C100.TXT`
(`usb_photo`, `usb_video`, `usb_meter`).

## VBUS state machine

0x1f3d4, in the peripheral task, reads VBUS on GPIO 70 [read]:

- state 0: nothing plugged in. An insert edge posts the personality and goes to state 1.
- state 1: VBUS present (a charger, or a host that has not configured the device). After 0x41
  polls (~16 s) stock restores the full key table; a drive init cuts keys to power only until
  then.
- state 2: a host configured a drive session (the drive init's SET_CONFIG callback). Buttons
  and the switch do nothing. Unplugging posts 0x700f and the camera powers off [read; seen,
  V1.1.3].

The camera enumerates only when the cable goes into a camera that is already on; powering on
with the cable in gives no USB activity [seen, V1.1.3].

## What the patch changes

- Unplug in state 1 (a charger, or webcam): a webcam is torn down with 0x8005; a drive gets
  stock's PHY off only, because 0x8002 would uninit the SD driver under the camera's own file
  system.
- Before a webcam init the patch clears [0x124294], the SET_CONFIG callback a drive init leaves
  behind. Its only writer is 0x4d65c (called only from the drive init) and its only reader
  0x4d054, which skips NULL [read]. Without that, a webcam session after a drive init would
  enter state 2, power the sensor down and power off at unplug.
- No auto power-off in states 1 and 2 (stock: state 2 only).

Drive then webcam in one power cycle does not work [seen, V1.1.3]: after a drive init on a
charger and an unplug, a webcam plug-in reports `drv_l2_usbd_uvc_init ok`, but the host gets
only resets and gives up (`Device not responding to setup address`). The drive setup is never
undone, and the webcam init does not replace it [inferred]. Power-cycle in between.

## Eject

When the PC ejects the drive (SCSI START STOP UNIT with LoEj), stock sets LUN 0 to 0xffaa and
calls the eject callback, which uninits the SD driver. The patch wraps the callback: it marks the
card as off limits before the uninit, remounts it on the next tick, runs `CMD.TXT`, flushes
`LOG.TXT`, waits 2 s, then writes LUN 0 = 1 so the host sees the card again, read fresh [read].

On the camera: the host saw the card return about 4 s after the eject, with `[v5] remount 0`,
`[v5] eject`, `[v5] handback` in `LOG.TXT` [seen, V1.1.3]. With `set led=off`, `set
sound=off`, `reboot` in `CMD.TXT`, the eject ran them and the camera restarted by itself; with
the cable still in, it came back as the drive 7 s after the eject [seen, V1.1.3].

## Audio

The webcam descriptor includes a UAC1 microphone (mono, 16-bit, 16 kHz, iso EP 0x87, alt
setting 5). It enumerates as an ALSA card [seen, V1.1.3]. Capture was never tried. In the code
the webcam path does not start the audio ADC or feed EP 0x87 [read], so expect silence or
nothing. Not implemented here.
