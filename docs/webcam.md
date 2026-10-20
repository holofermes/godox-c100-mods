# Webcam

Tags: [read] = read in the firmware, [seen] = observed on a camera, [inferred] = deduction.

## Stock and base image

The UVC descriptor offers one format, MJPEG, one frame size (device descriptor file 0xf3bc8,
config file 0xf3a84). Probe/commit (0x52470) accepts only format 1, frame 1, 333333 (30 fps).
The camera terminal exposes no controls; the processing unit only brightness, so the host
cannot set exposure [read].

Stream start runs the encoder in APMode 0x106, whose second (JPEG) path feeds the UVC endpoint.
0x106 asks for 1280x720 at 0xf0e4/0xf0f0, which the ISP takes as an unscaled crop from the
middle of the 1920x1080 sensor: `[V]x:320,y:180, Cw:1280,Ch:720, ... crop:1` in the log
[read, seen]. So stock-style 720p sees two thirds of the field of view.

## 1080p

Four changes (V6_DATA in `tools/patch.py`):

| file | RAM | stock | new |
| --- | --- | --- | --- |
| 0xf3b0a | 0xe390a | `00 05 d0 02` | `80 07 38 04`: frame descriptor 1920x1080 |
| 0xf3b16 | 0xe3916 | `00 20 1c 00` | `00 48 3f 00`: dwMaxVideoFrameBufferSize |
| 0x1f2e4 | 0xf0e4 | `mov r2,#0x2d0` | `ldr r2,[pc,#0x7a8]`, the stock literal 0x438 at 0xf894 (1080 has no ARM immediate) |
| 0x1f2f0 | 0xf0f0 | `mov r3,#0x500` | `mov r3,#0x780` |

On the camera [seen]: the host lists MJPEG 1920x1080 only; the log shows
`arg->Target=(1920 1080)`, `[V]x:0,y:0 ... crop:0,scaler:0`. 26.7 fps, frames 96-100 KB
(rate control; each ends in EOI plus up to 31 bytes of DMA padding), about 21 Mbit/s. The
200 KiB JPEG buffer holds them.

Back to 720p (stock's centre crop): delete the four V6_DATA rows in `tools/patch.py` and the four
`V6D` rows in `tests/test_v5.py`, then build. A full-view 720p, or both sizes for the host to
pick, needs a second frame descriptor and a scaled second path: about 400 B of new data and
code, more than the payload region has left.

## Left/right while streaming

Without the gate, pressing left or right in Video while the PC streams freezes the stream for
good on that connection; a host that reopens the device (ustreamer does) gets frames again,
but too bright until a power cycle [seen].

Cause, from the log [seen]: the key changes the aspect index and the video mode exits and
re-enters (`state_video_record_exit`, `ENTER MODE [101]`). Re-entry runs
`video_encode_sensor_on` for recording (1440x1080 crop at x 240), which stops the webcam's
encoder (free heap rises 2.5 MB as its buffers go), and sets AE metering for recording (meter
0x6 with the user weight table; the webcam ran meter 0x5 without it).

Fix (`v6_aspect_gate`): the three callers of the aspect step 0x21318 (photo 0x2ea90, video
0x36f4c, 0x36f7c) go through a gate. While the host streams (EP5 alt at [0x123d04] != 0) it
skips the step; the caller then sees index == stored setting and does not re-enter [read]. On
the camera: a 60 s stream with left, right and shutter pressed kept 26.7 fps with no gap over
43 ms and no re-entry in the log [seen]. There is no setting to turn the gate off.

## Recording while streaming

Without the gate, stopping a recording started while the PC streams powers the camera off
[seen with the base patch; cause not proven]. The likely cause is the app's record stop and the
host's stream re-arm working on the same encoder without a lock [inferred].

Fix (`v6_rec_gate`): the record start branch at 0x356e0 goes through the same gate; blocked, it
returns as if nothing happened [read]. Untested: a recording started before the PC opens the
stream can still be stopped during it.

## Using it

    ustreamer --device=/dev/v4l/by-id/usb-GENERAL_GENERAL_-_UVC-video-index0 \
        --format=MJPEG --encoder=HW --resolution=1920x1080 --host=0.0.0.0 --port=8080

`--encoder=HW` passes the camera's JPEG frames through untouched.
