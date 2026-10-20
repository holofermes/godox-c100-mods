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
`[V]x:0,y:0 ... crop:0,scaler:0` [seen]. That build changed frame 1 in stock's descriptor in
place; the frame-1 bytes are the same in the copy below.

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
  descriptor follows at 0xe3b14. `v6_cfg` in `src/v6.S` is a copy in the RAM gap, 512 B, with
  frames 2..6 after frame 1; `tools/patch.py` checks it byte for byte against stock's. The two
  words that held 0xe39d0 point at the copy: register_desc(1) in usb_uvc_init (pool 0x3ef00),
  and the write of the alt-3 wMaxPacketSize at 0x52370 (pool 0x52488, + 0xbe, where that field
  moved). GET_DESCRIPTOR (0x508b4) rewrites byte 1 of it, so it has to be in RAM.
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

Open until the camera test: the scaler beyond 6x (256x144 is 7.5x, 128x72 is 15x, stock uses
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

## Using it

    ustreamer --device=/dev/v4l/by-id/usb-GENERAL_GENERAL_-_UVC-video-index0 \
        --format=MJPEG --encoder=HW --resolution=1920x1080 --host=0.0.0.0 --port=8080

`--encoder=HW` passes the camera's JPEG frames through untouched; `--resolution` picks the frame
size. After a re-plug ustreamer reopens the device by itself [seen].
