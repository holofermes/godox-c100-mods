#include "v5.h"
#include "fw.h"

/* Display off, LED gate, sound gate, long press, reboot, drive handback
 * (docs/hooks.md, docs/usb.md). */

#define CHIME_LATE 15000     /* ms since boot; later than this a held chime is dropped */
#define REBOOT_WAIT 3000     /* ms to wait for the 0x7007 power cut */
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

/* Posted rather than calling fw_reboot: every stock caller of 0x1fbac runs
 * in the AP task, the video state stops its pipeline (0x353f4) before it, and
 * if the power does not drop (as it might with VBUS present) the endless loop
 * at its end parks the AP task while the peripheral task keeps the keys and
 * the Off switch. Returns only when refused or still powered, logging
 * "reboot X": X = the unmount error, 3 = busy, 2 = post failed, 1 = still on. */
void act_reboot(void)
{
	u32 app = W_APP_STATE;
	s32 r;

	if (B_CAPTURE_BUSY || (B_REC_FLAGS & 0xa) || (app != 0x101 && app != 0x102)) {
		v5_log("reboot %x", 3);
		return;
	}
	v5_log("reboot");
	log_flush();
	r = fw_unmount(DRIVE_G);
	if (!r) {
		/* keeps the payload off the unmounted card; the tick remounts if we are still here */
		st.card_stale = 1;
		r = 2;
		if (!apq_post(MSG_REBOOT, 0, 0, 1)) {
			fw_delay(REBOOT_WAIT);
			r = 1;
		}
	}
	v5_log("reboot %x", r);
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
