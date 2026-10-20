#include "v5.h"
#include "fw.h"

/* Shared state for the patch's features. The LED layout is read by stubs.S in asm:
 * do not reorder struct v5_led in v5.h. */

struct v5_led led;
struct v5_state st;

/* stubs.S reads st.booted (the arm flag) and writes st.sound_pending */
_Static_assert(__builtin_offsetof(struct v5_state, sound_pending) == 5, "st.sound_pending");
_Static_assert(__builtin_offsetof(struct v5_state, booted) == 6, "st.booted");

/* Boot-time values, before C100.TXT is read. LED off levels default to 0
 * (GPIO 43 is lit at 1, docs/hardware.md), and C100.TXT can override both.
 * Runs once, on the first mount, before the gate arms: the gpio_set gate
 * records want43 and want95 only once armed, so they start from what stock
 * left on the pins (stock reads 95 back the same way, 0x1f544). Later mounts
 * leave them to the gate and off to cfg_apply. */
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
