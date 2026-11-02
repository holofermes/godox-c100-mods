# Display

A [ghee](https://github.com/holofermes/ghee) scene drives the OLED, and shows the camera feed on a
1-bit panel, using an [frc](https://en.wikipedia.org/wiki/Frame_rate_control) filter.

![the C100's picture and ghee's greys; the 16 frc frames one after the other and the panel adding them up; the case's back with its OLED boxed, and the OLED close up](images/display/display.webp)

## The console

You can tweak the picture live by using the console. To open it, tunnel to the Pi with
`ssh -L 8090:localhost:8090 pi@PI`, then go to http://localhost:8090/.

![ghee's console with the camera scene open](images/display/console.png)
