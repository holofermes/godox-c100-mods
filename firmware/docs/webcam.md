# Webcam

Tags: [read] = read in the firmware, [seen] = observed on a camera, [inferred] = deduction.

## Stock and base image

The UVC descriptor offers one format, MJPEG, one frame size (device descriptor file 0xf3d14,
config file 0xf3bd0). Probe/commit (0x52644) accepts only format 1, frame 1, 333333 (30 fps).
The camera terminal exposes no controls; the processing unit only brightness, so the host
cannot set exposure [read].

Stream start runs the encoder in APMode 0x106, whose second (JPEG) path feeds the UVC endpoint.
0x106 asks for 1280x720 at 0xf0e4/0xf0f0 [read], which the ISP takes as an unscaled crop from
the middle of the 1920x1080 sensor [inferred]. So stock-style 720p sees two thirds of the field
of view.

## 1080p

APMode 0x106's target becomes 1920x1080, the whole sensor (V6_DATA in `tools/patch.py`):

| file | RAM | stock | new |
| --- | --- | --- | --- |
| 0x1f2e4 | 0xf0e4 | `mov r2,#0x2d0` | `ldr r2,[pc,#0x7a8]`, the stock literal 0x438 at 0xf894 (1080 has no ARM immediate) |
| 0x1f2f0 | 0xf0f0 | `mov r3,#0x500` | `mov r3,#0x780` |

Frame 1 of the descriptor is 1920x1080 (below). On the camera: MJPEG 1920x1080 at 26.7 fps over
7 minutes, no gap between frames over 52 ms; the log shows `arg->Target=(1920 1080)`,
`[V]x:0,y:0 ... crop:0,scaler:0` [seen]. The frame rate was measured with frame 1 changed in
place in stock's descriptor; the copy below has the same frame-1 bytes.

## Frame sizes

Six MJPEG frames, all 16:9, all the whole sensor scaled down, 30 fps each:

| bFrameIndex | size |
| --- | --- |
| 1 | 1920x1080, the default |
| 2 | 1280x720 |
| 3 | 640x360 |
| 4 | 320x180 |
| 5 | 256x144 |
| 6 | 128x72 |

The host picks one, for example `ustreamer --resolution 256x144` or
`v4l2-ctl --set-fmt-video=width=256,height=144,pixelformat=MJPG`. Not yet tested on the camera.
No 4:3 sizes: the stock code letterboxes or squashes them [read].

How [read]:

- Stock's config descriptor (0xe39d0, 0x142 B) has no room for more frames: the device
  descriptor follows at 0xe3b14. `v6_cfg` in `src/v6.S` is a copy in the RAM gap, 538 B, with
  frames 2..6 after frame 1 and the extension unit (below); `tools/patch.py` checks it byte for
  byte against stock's. The two words that held 0xe39d0 point at the copy: register_desc(1) in
  usb_uvc_init (pool 0x3ef00), and the write of the alt-3 wMaxPacketSize at 0x52370 (pool
  0x52488, + 0xd8, where that field moved). GET_DESCRIPTOR (0x508b4) rewrites byte 1 of it, so it
  has to be in RAM. In UVC mode nothing else reads the descriptor [read].
- Probe/commit (0x527d4) accepts bFrameIndex 0..6, stock only 1. The SET_CUR handler copies the
  host's request to 0xe3b2c before it checks it.
- On stream start, sensor_on(0x106) builds the geometry in 0xf004. At 0xf510 `v6_size` gives the
  second path the size of the committed frame (0, or anything above 6, counts as frame 1), and
  0xf4e0 sets the display size from the second path, as stock APMode 0x107 does at 0xf320. Path
  0 and the clip stay 1920x1080. The encode task sets the Pscaler unit 1 output, the 64-line
  strips and the JPEG header from that.
- `v6_size` also sets the zoom of both paths to 100. Stock does that only on Photo and Video
  entry (0x12d68), and below 100 a small output becomes a centre crop (emulator).

In an emulator run of the whole chain, from 0xf98c through the encode task's scaler setup, path 0
is 1920x1080 for every size, and the unit-1 output, the rear size and the JPEG header equal the
frame. That the UVC JPEG comes from unit 1 is [inferred]: unit 0 is not programmed under 0x106,
and unit 1's interrupt feeds the strip handler [read].

Not yet tested on the camera: the scaler beyond 6x (256x144 is 7.5x, 128x72 is 15x, stock uses
about 6x), the JPEG engine at widths of 256 and below with 2 or 3 strips a frame, the frame rate
at small sizes, the free heap across size switches, Metering at small sizes, and hosts other than
Linux.

## Left/right while streaming

Without the fix, left or right in Video while the PC streams changes the aspect index, and the
video mode exits and re-enters [read]. Entering Video runs `video_encode_sensor_on` for
recording and sets AE meter 0x6 with the user weight table (`LOG.TXT`) [seen]; while a PC
streams, that stops the webcam's encoder and the stream freezes [inferred].

Fix (`v6_aspect_gate`): the three callers of the aspect step 0x213e0 (photo 0x2ec48, video
0x37104, 0x37134) go through a check. While the host streams (EP5 alt at [0x123d3c] != 0) it
skips the step; the caller then sees index == stored setting and does not re-enter [read].
There is no setting to turn this fix off.

## Recording while streaming

Without the fix, a recording can start while the PC streams; its stop and the host restarting
the stream then work on the same encoder without a lock [inferred].

Fix (`v6_rec_gate`): the record start branch at 0x35898 goes through the same check; while the
host streams, record start returns as if nothing happened [read]. The check is on record start
only: a recording started before the PC opens the stream can still be stopped during it [read].

## Extension unit

A PC sends `CMD.TXT` lines to the camera through a UVC extension unit (`tools/c100cmd.py`,
README).

| | |
| --- | --- |
| unit ID | 6, between the processing unit (5) and the output terminal (3) |
| GUID | `{21118ec3-89d6-4bb3-8015-7611dd2b1f87}` |
| control | selector 1, 32 B: one line, NUL-padded, printable ASCII |
| GET_INFO | 0x03: GET and SET |
| GET_LEN | 32 |
| GET_CUR | the line still waiting to be run; 32 zeros once the camera took it, or before any |
| SET_CUR | a line, taken when nothing waits and the patch is loaded |

Any other request (GET_MIN, GET_MAX, GET_RES, GET_DEF, others), another selector or a wLength
other than the size stalls EP0 with a request error code, as stock does for its own controls: 6
for a selector, 7 for a request or length. SET_CUR is also refused with 2 while the patch is not
loaded (no card, a failed overlay load) and with 1 while the last line still waits. Linux turns
6 and 7 into EIO, 2 into EACCES (EILSEQ on 6.1), 1 into EBUSY.

How [read]:

- Linux reaches an extension unit only on the chain it walks back from the output terminal
  (drivers/media/usb/uvc/uvc_driver.c, checked on v7.3, v6.1 to v6.12 and the Raspberry Pi
  6.1 to 6.18 branches), hence its place in the chain. It sends nothing to it by itself; the first
  query of a control sends GET_LEN, then GET_INFO.
- The requests arrive in the setup handler 0x507ac that usb_uvc_init installs, run by the USB
  device task. Three branches send wIndex 0x0600 (unit 6, interface 0) to `src/xu.S` in the RAM
  gap: 0x50980 (class GET), 0x507f0 (class SET), 0x52570 (the SET_CUR data stage in 0x524b8).
  Everything else runs as stock. The answers go out through stock's EP0 send 0x509d4, the stalls
  through 0x50930 and the error code at 0x12440a, which the host reads back with GET_CUR on unit
  0 (0x51098).
- SET_CUR copies the line to a 32 B mailbox in the gap and sets a flag; nothing else happens in
  the USB task. The patch's tick (peripheral task, about every 250 ms) takes the line once the
  card is the camera's, zeroes the mailbox, logs `xu: <line>` and runs it as a `CMD.TXT` line. A
  line that arrives while a firmware update is on the card is dropped (`xu: <line> not run, update
  on the card`).
- Being in the gap, it answers without the overlay; without it nothing takes a line, so SET_CUR
  is refused.

In the emulator, through the stock handlers: every request above, the refusals, and 1,875 other
class requests on interfaces 0 and 1 answered as on the image without the patch, among them the
processing unit's brightness, stock's only control (stock reads its SET_CUR value big-endian,
0x527f0).

## Using it

    ustreamer --device=/dev/v4l/by-id/usb-GENERAL_GENERAL_-_UVC-video-index0 \
        --format=MJPEG --encoder=HW --resolution=1920x1080 --host=0.0.0.0 --port=8080

`--encoder=HW` passes the camera's JPEG frames through untouched; `--resolution` picks the frame
size. After a re-plug ustreamer reopens the device by itself [seen].
