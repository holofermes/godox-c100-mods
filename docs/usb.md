# USB

Tags: [read] = read in the firmware, [seen] = observed on a camera, [inferred] = deduction.

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
  and the switch do nothing. Unplugging posts 0x700f and the camera powers off [read; seen].

The camera enumerates only when the cable goes into a camera that is already on; powering on
with the cable in gives no USB activity [inferred].

## What the patch changes

- Unplug in state 1 (a charger, or webcam): a webcam is torn down with 0x8005; a drive gets
  stock's PHY off only, because 0x8002 would uninit the SD driver under the camera's own file
  system.
- Before a webcam init the patch clears [0x124294], the SET_CONFIG callback a drive init leaves
  behind. Its only writer is 0x4d65c (called only from the drive init) and its only reader
  0x4d054, which skips NULL [read]. Without that, a webcam session after a drive init would
  enter state 2, power the sensor down and power off at unplug.
- No auto power-off in states 1 and 2 (stock: state 2 only).

Drive then webcam in one power cycle does not work: after a drive init (on a PC or a charger)
and an unplug, the drive setup is never undone, and a webcam init does not replace it
[inferred]. Power-cycle in between.

## Eject

When the PC ejects the drive (SCSI START STOP UNIT with LoEj), stock sets LUN 0 to 0xffaa and
calls the eject callback, which uninits the SD driver. The patch wraps the callback: it marks the
card as off limits before the uninit, remounts it on the next tick, runs `CMD.TXT`, flushes
`LOG.TXT`, waits 2 s, then writes LUN 0 = 1 so the host sees the card again, read fresh [read].

On the camera, with `CMD.TXT` written over USB: after the eject the camera remounted the card,
deleted the file and ran it, and the host saw the card again about 4 s after the eject, with
`[v5] remount 0`, `[v5] eject`, `[v5] handback` in `LOG.TXT` [seen]. `display off`, `led off`,
`display on` and `led on` switched the LCD and LED; `reboot` restarted the camera, and with the
cable still in it came back as the drive about 5 s after the eject [seen].

## One boot as the webcam

CMD.TXT `webcam [MIN]` restarts the camera as the webcam whatever the switch; with MIN, 1 to
1440, it restarts again MIN minutes after that boot, and that boot follows the switch. Not yet
tested on the camera.

- The command first makes the checks `reboot` makes (no capture, no recording, photo or video
  state; else `reboot 3` and nothing is written). Then it writes `WEBCAM.ONE` in the card root
  holding MIN (0 without it), flushes the card and reboots the way `reboot` does
  (`src/webcam.c`). If the reboot fails with the card still mounted, the file is deleted again.
- On the next boot load_settings, after the update check, reads `WEBCAM.ONE`, deletes it,
  flushes, and acts on it only if the delete worked: the webcam on every switch position for
  this boot, and with MIN a reboot MIN minutes later, tried again every minute while refused. A
  file that cannot be deleted is left alone and the boot is a normal one; one that is not a
  number from 0 to 1440, or is over 16 B, is deleted and ignored. The file is gone before the
  webcam boot starts, so it never acts twice.
- `LOG.TXT`: `webcam N` and `reboot` from the command; `webcam: boot N`, then `webcam: time up`
  and `reboot`; `webcam: rm fail`, `webcam: bad X`, `webcam: bad file`, `webcam: write fail`,
  `webcam: marker removed`.
- The command comes in over a drive session, and a webcam session after a drive session in the
  same power cycle has failed so far (above). The webcam boot relies on the reboot resetting the
  USB side [inferred]; that is the first thing the camera test has to show.
- In Photo this is the same path as `usb_photo=webcam`, also not tested on the camera. Metering
  is a sub-mode of the photo state (0x2ebe4), so the reboot is not refused there [inferred].

## Audio

The webcam descriptor includes a UAC1 microphone (mono, 16-bit, 16 kHz, iso EP 0x87, alt
setting 5). The webcam path does not start the audio ADC or feed EP 0x87 [read], so expect
silence or nothing. Not implemented here.
