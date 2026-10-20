#include "v5.h"
#include "fw.h"

/* LED gate state, overlay data. stubs.S reads its layout in asm (armed only):
 * do not reorder struct v5_led in v5.h. st lives in the gap (loader.c). */

struct v5_led led;

/* Boot-time values, before C100.TXT is read. LED off levels default to 0
 * (GPIO 43 is lit at 1, docs/hardware.md), and C100.TXT can override both.
 * Runs once, on the first mount that loads the overlay, before the gate arms:
 * the gpio_set gate records want43 and want95 only once armed, so they start
 * from what stock left on the pins (stock reads 95 back the same way,
 * 0x1f5b4). Later mounts leave them to the gate and off to cfg_apply. */
void led_init(void)
{
	static u8 inited;

	if (inited)
		return;
	inited = 1;
	led.off = 0;
	led.want43 = fw_gpio_read(PIN_43) != 0;
	led.want95 = fw_gpio_read(PIN_95) != 0;
	led.off43 = 0;
	led.off95 = 0;
}
