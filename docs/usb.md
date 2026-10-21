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
  system. A stream still running is stopped first the way the host's alt 0 stops it: stock's
  alt callback 0x3e5e0(0) has `uvc_task` hand its frame back and post the stream stop 0x8008,
  then 0.5 s later the uninit [read]. A pulled cable only gives the USB core a bus suspend,
  which stock prints and ignores (0x52428), and the frame on EP5 never completes.
- The uninit no longer closes the encoder (its `bl 0x3ec00` at 0x3ef4c is patched out). That close
  sends 0x800000, which the encoder answers by freeing the UVC session (0x17900) but never
  acknowledges (0x19864), so 0x3ec00 waited 5 s and left the session flagged open (`OSMbox ack
  Fail!!!:0x800000`) [read, seen]; run under a frame still on EP5 it turned the camera off [seen].
  After the host-style stop the session stays open, as after every host stop, and the next
  stream reuses it.
- Before a webcam init the patch clears bit 0x200 of 0xd0000008 (`src/uvc.S`, at the start of
  usb_uvc_init). 0xbdf04, the USB block off that the uninit and every unplug run, sets 0x8200;
  the drive init clears both (0xbdd40), the webcam init only 0x8000 (0xbdf3c) [read]. With 0x200
  left set the init printed `rSYS_CTRL_NEW =0x29c` instead of 0x9c and the host saw a
  full-speed device that answered nothing, so no webcam came back after an unplug [seen]. The
  stop-first teardown and the re-init are not yet tested on the camera.
- Before a webcam init the patch clears [0x124294], the SET_CONFIG callback a drive init leaves
  behind. Its only writer is 0x4d65c (called only from the drive init) and its only reader
  0x4d054, which skips NULL [read]. Without that, a webcam session after a drive init would
  enter state 2, power the sensor down and power off at unplug.
- No auto power-off in states 1 and 2 (stock: state 2 only).

Drive then webcam in one power cycle has not worked: after a drive init (on a PC or a charger)
and an unplug, a webcam init did not enumerate. A drive unplug runs 0xbdf04 too, so the bit
0x200 that the webcam init now clears may have been the cause [inferred]; not yet tested on the
camera. Until then, power-cycle in between.

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

## Restart

`reboot`, `webcam`, `drive` and the `webcam MIN` timer restart the camera the same way
(`act_reboot` in `src/act.c`): flush `LOG.TXT`, unmount the card, kick the watchdog once, then
call stock's 0x1fbac in the peripheral task. 0x1fbac does not cut the power: it sets the SoC
watchdog to period 7 (0xd0000028), sets [0x12166e], arms an RTC alarm 2 s ahead and spins
[read]. The watchdog runs out and resets the camera, and the alarm powers it back up [inferred;
the USB drop 0.4-1.1 s after the command is the reset, seen]. So nothing may kick the watchdog
once 0x1fbac runs. Stock kicks it in two places [read]: the peripheral task's tick, which never
runs again once that task spins in 0x1fbac, and `uvc_task` (0x3e6d4), every second while the
webcam is up, whose kick the patch skips while [0x12166e] is set (`src/wdt.S`).

- Stock calls 0x1fbac from the app on 0x7007. Posting 0x7007 and leaving it to the app failed in
  a drive session with the switch on Video: after the host's eject stock's card check also
  unmounted the card and told the app it was gone (0x3dc14, 0x5016), and the app never ran
  0x1fbac; the camera went dark and did not restart [seen]. Calling it from the peripheral task
  needs no app. Skipped from stock's 0x7007 case: the video state's 0x353f4, which only clears
  the recording time on the display [read]. Not yet tested on the camera.
- With the webcam up and no kick skip, the restart did not come: `uvc_task` kept the watchdog
  fed [seen]. Tearing the webcam down first (the uninit 0x8005) while a host streamed turned the
  camera off [seen] (see the unplug above).
- `LOG.TXT`: `reboot`; `reboot 3` refused (capture, recording, or not in the photo or video
  state), `reboot X` the unmount failed with error X. Nothing is logged after the restart starts.

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

## One boot as the drive

`drive` restarts the camera as the USB drive whatever the switch and `C100.TXT` say, for that
boot only. It comes from `CMD.TXT` or from the PC while the camera is the webcam (README,
docs/webcam.md). Not yet tested on the camera.

- The same path as `webcam` without minutes (`src/webcam.c`): the checks `reboot` makes, then
  `DRIVE.ONE` in the card root, flushed, then the reboot; deleted again if the reboot fails with
  the card still mounted.
- The next boot deletes `DRIVE.ONE` first and acts only if the delete worked: the drive on every
  switch position, no timer. Its contents are not read; one over 16 B is deleted and ignored, one
  that cannot be deleted gives a normal boot.
- Both markers on the card: both are deleted and the drive wins; the webcam boot and its timer
  are dropped (`webcam: ignored, drive boot`). The drive is the way back to the card from a PC,
  and it never restarts by itself.
- While the webcam streams the reboot is not refused [read]. The app stays in the photo or video
  state: its state word 0x13288c has one writer, the state dispatcher 0x29d64, and no webcam
  message changes it. Streaming sets neither the capture nor the record flags, and the card stays
  the camera's, so the unmount works. The 0x7007 handlers (video 0x370a4, photo 0x2ec1c) wait for
  nothing in the USB task. On the camera the restart itself failed with the webcam up (Restart,
  above); fixed, not yet tested. Whether the drive then comes up with the cable still in is for the
  camera test.
- `LOG.TXT`: `drive` and `reboot` from the command, after `xu: drive` when it came from the PC;
  `drive: boot`; `drive: rm fail`, `drive: bad file`, `drive: write fail`, `drive: marker
  removed`.

## Audio

The webcam descriptor includes a UAC1 microphone (mono, 16-bit, 16 kHz, iso EP 0x87, alt
setting 5). The webcam path does not start the audio ADC or feed EP 0x87 [read], so expect
silence or nothing. Not implemented here.
