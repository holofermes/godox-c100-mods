#include "v5.h"
#include "fw.h"

#define CFG_MAX 4096
#define RING 8192 /* power of two, for log_grow */

extern char __gap_start[], __gap_end[], __ovl_start[], __ovl_end[]; /* link.ld */

/* The overlay's entry, from v5_set (loader.c) once the overlay is loaded: the
 * 0x37fa0 stub at the end of the VERSION.TXT writer on the normal mount branch,
 * storage task (task_storage_service_entry), 4 KB stack. Runs on every fresh
 * mount: power-on and every card re-insert. */
void load_settings(void)
{
	static u8 grown;
	struct v5_cfg c;
	char *text;
	const char *how;
	u32 irq;
	s32 n;

	/* stock just mounted the card: its cache is fresh, stop any remount retry */
	st.card_stale = 0;
	led_init();
	cfg_defaults(&c);
	if (!grown) {
		char *b = fw_malloc(RING);
		if (b) {
			log_grow(b, RING);
			grown = 1;
		}
	}

	n = fs_read(P_CFG, &text, CFG_MAX);
	how = n < 0 ? "bad" : text ? "ok" : "none";
	if (text) {
		cfg_parse(&c, text, n);
		fw_free(text);
	}
	/* Parsed aside and copied in one step: armed code in the peripheral and
	 * USB tasks reads cfg while this runs in the storage task (a card
	 * re-insert), and must not see the defaults or a half-read file. */
	irq = v5_irq_save();
	v5_memcpy(&cfg, &c, sizeof(c));
	v5_irq_restore(irq);

	cfg_apply();

	/* bytes in the gap + bytes in the overlay */
	v5_log("v5 %u+%u B, C100.TXT %s", (u32)__gap_end - (u32)__gap_start, (u32)__ovl_end - (u32)__ovl_start,
	       how);
	/* cfg: display led sound log, then usb photo video meter */
	v5_log("cfg %u%u%u%u usb %u%u%u", cfg.display, cfg.led, cfg.sound, cfg.log,
	       cfg.usb[POS_PHOTO], cfg.usb[POS_VIDEO], cfg.usb[POS_METER]);
	/* arms every hook (stubs.S); never reached on a boot that flashes */
	st.booted = 1;
}
