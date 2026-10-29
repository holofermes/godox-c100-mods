#include "v5.h"
#include "fw.h"

/* Display off, LED gate, sound gate, long press, reboot, drive handback
 * (docs/hooks.md, docs/usb.md). */

#define CHIME_LATE 15000     /* ms since boot; later than this a held chime is dropped */
#define HANDBACK_SETTLE 2000 /* ms for a posted capture or record to show as busy */

/* the gpio_set and v5_get95 stubs in stubs.S read struct v5_led with ldrb/strb
 * at these offsets */
_Static_assert(__builtin_offsetof(struct v5_led, off) == 0, "led.off");
_Static_assert(__builtin_offsetof(struct v5_led, want43) == 1, "led.want43");
_Static_assert(__builtin_offsetof(struct v5_led, want95) == 2, "led.want95");
_Static_assert(__builtin_offsetof(struct v5_led, off43) == 3, "led.off43");
_Static_assert(__builtin_offsetof(struct v5_led, off95) == 4, "led.off95");
_Static_assert(sizeof(struct v5_led) == 5, "led size");

static u8 lcd_off; /* 0 = LCD ON, as the HT1621 init leaves it */
static u8 snd;     /* what act_sound_gate answers once C100.TXT is known */
static u8 pend;    /* cfg_apply ran outside the peripheral task */
static u8 hb;      /* drive handback waiting for the camera to go idle */
static u32 hb_at;

/* The HT1621 bus is bit-banged without a lock by seg_refresh in the
 * peripheral task, and GPIO 43 shares its bank, so LCD and LED writes happen
 * only there. */
static int in_periph(void)
{
	u32 t = PERIPH_TCB;

	return t && CUR_TCB == t;
}

/* Re-send the levels stock last asked for; the gpio_set stub swaps in the off
 * levels while led.off is set. IRQs off so another task cannot write a pin
 * between the read of want and the stub storing it back. */
static void led_push(void)
{
	u32 c = v5_irq_save();

	fw_gpio_set(PIN_43, led.want43);
	fw_gpio_set(PIN_95, led.want95);
	v5_irq_restore(c);
}

void act_display(u8 on)
{
	on = on != 0;
	if (!in_periph() || lcd_off == !on)
		return;
	fw_ht_cmd(on ? HT_LCD_ON : HT_LCD_OFF);
	lcd_off = !on;
	v5_log("lcd %s", on ? "on" : "off");
}

void act_led(u8 on)
{
	on = on != 0;
	if (!in_periph() || led.off == !on)
		return;
	led.off = !on;
	led_push();
	v5_log("led %s", on ? "on" : "off");
}

void act_sound(u8 on)
{
	on = on != 0;
	if (snd == on)
		return;
	snd = on;
	v5_log("sound %s", on ? "on" : "off");
}

/* the sound id (2 or 3) the S1 stub held while disarmed; in stock only the
 * startup chime plays id 2 and nothing plays id 3 */
static void chime(void)
{
	u32 c;
	u8 id;

	if (!st.booted || !st.sound_pending)
		return;
	c = v5_irq_save();
	id = st.sound_pending;
	st.sound_pending = 0;
	v5_irq_restore(c);
	if (snd && v5_ms() < CHIME_LATE) {
		v5_log("chime %u", id);
		fw_play_effect(id);
	}
}

/* Sound applies from any task. The LCD and LED only from the peripheral task:
 * a call from the mount hook (storage task) sets pend and act_tick catches up on
 * the next pass. Compares before writing, so repeat calls cost nothing. */
void cfg_apply(void)
{
	act_sound(cfg.sound);
	chime();
	if (!in_periph()) {
		pend = 1;
		return;
	}
	pend = 0;
	if (led.off43 != cfg.led_off43 || led.off95 != cfg.led_off95) {
		led.off43 = cfg.led_off43;
		led.off95 = cfg.led_off95;
		if (led.off)
			led_push();
	}
	act_led(cfg.led);
	act_display(cfg.display);
}

/* S1, play_effect entry, called from the ARM stub in whatever task plays the
 * effect (the AP task in stock). Armed only: before that the stub holds ids 2
 * and 3 for chime() itself. */
s32 act_sound_gate(s32 id)
{
	(void)id;
	return snd;
}

/* K1: the Select (F) handler with count > 0x18, peripheral task. Stock calls
 * it every 28 scans while the key is held; only the first call of a hold has
 * the held latch clear. */
void act_long_select(void)
{
	if (B_F_HELD)
		return;
	st.toggle_req = 1;
	v5_log("toggle");
}

/* capturing, recording, or not in the video or photo state: no reboot now */
int reboot_busy(void)
{
	u32 app = W_APP_STATE;

	return B_CAPTURE_BUSY || (B_REC_FLAGS & 0xa) || (app != 0x101 && app != 0x102);
}

/* Restart through stock's 0x1fbac, called here in the peripheral task. It
 * does not cut the power: it sets the SoC watchdog to period 7 and sets
 * [0x12166e], arms an RTC alarm 2 s ahead and spins. The watchdog runs out
 * and resets the camera, and the alarm powers it back up. The watchdog has two
 * kickers: this task's 0x6000 tick, which never runs again once we spin in
 * 0x1fbac, and uvc_task while the webcam is up, whose kick src/wdt.S skips
 * once [0x12166e] is set. So the restart does not depend on the app task:
 * stock calls 0x1fbac from the app on 0x7007, and after the host's eject in a
 * drive session with the switch on Video the app does not run it. The kick
 * right before starts the watchdog's time there, so the alarm is armed long
 * before it runs out.
 * Skipped from stock's 0x7007 case: the video state's 0x353f4, which only
 * clears the recording time on the display. Returns only when refused or when
 * the unmount fails, logging "reboot X": X = 3 busy, else the unmount
 * error. */
void act_reboot(void)
{
	s32 r;

	if (reboot_busy()) {
		v5_log("reboot %x", 3);
		return;
	}
	v5_log("reboot");
	log_flush();
	r = fw_unmount(DRIVE_G);
	if (r) {
		v5_log("reboot %x", r);
		return;
	}
	/* keeps the payload off the unmounted card until the reset */
	st.card_stale = 1;
	fw_wdt_kick();
	fw_reboot();
}

/* From v5_tick after the host ejected the drive, once the tick has remounted
 * the card and run CMD.TXT and LOG.TXT. The LUN stays ejected (the host sees
 * no medium) until act_tick finds the camera idle: a capture or recording that
 * CMD.TXT started must not write while the host owns the card. */
void act_host_released(void)
{
	if (H_LUN0 != LUN_EJECTED || st.usb_active != USB_DRIVE)
		return;
	hb = 1;
	hb_at = v5_ms() + HANDBACK_SETTLE;
	v5_log("eject");
}

/* Writing 1 makes the USB task's 1 s LUN poll re-init LUN 0 (status 4, then
 * UNIT ATTENTION), and the host reads the card fresh. */
static void handback(void)
{
	u32 c;

	if (H_LUN0 != LUN_EJECTED || st.usb_active != USB_DRIVE) {
		hb = 0;
		v5_log("eject %x", H_LUN0);
		return;
	}
	if ((s32)(v5_ms() - hb_at) < 0 || st.card_stale || B_CAPTURE_BUSY ||
	    (B_REC_FLAGS & 0xa))
		return;
	v5_log("handback");
	log_flush();
	fs_flush();
	c = v5_irq_save();
	if (H_LUN0 == LUN_EJECTED)
		H_LUN0 = 1;
	v5_irq_restore(c);
	hb = 0;
}

/* every v5_tick pass, peripheral task */
void act_tick(void)
{
	if (pend)
		cfg_apply();
	chime();
	if (hb)
		handback();
}
