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

Four changes (V6_DATA in `tools/patch.py`):

| file | RAM | stock | new |
| --- | --- | --- | --- |
| 0xf3c56 | 0xe3a56 | `00 05 d0 02` | `80 07 38 04`: frame descriptor 1920x1080 |
| 0xf3c62 | 0xe3a62 | `00 20 1c 00` | `00 48 3f 00`: dwMaxVideoFrameBufferSize |
| 0x1f2e4 | 0xf0e4 | `mov r2,#0x2d0` | `ldr r2,[pc,#0x7a8]`, the stock literal 0x438 at 0xf894 (1080 has no ARM immediate) |
| 0x1f2f0 | 0xf0f0 | `mov r3,#0x500` | `mov r3,#0x780` |

On the camera: MJPEG 1920x1080 at 26.7 fps over 7 minutes, no gap between frames over 52 ms;
the log shows `arg->Target=(1920 1080)`, `[V]x:0,y:0 ... crop:0,scaler:0` [seen].

Back to 720p (stock's centre crop): delete the four V6_DATA rows in `tools/patch.py` and the four
`V6D` rows in `tests/test_v5.py`, then build. A full-view 720p, or both sizes for the host to
pick, needs a second frame descriptor and a scaled second path, about 400 B of new data and
code; not built.

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

`--encoder=HW` passes the camera's JPEG frames through untouched. After a re-plug ustreamer
reopens the device by itself [seen].
