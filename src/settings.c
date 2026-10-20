#include "v5.h"
#include "fw.h"

#define CFG_MAX 4096
#define RING 8192 /* power of two, for log_grow */

extern char __v5_start[], __v5_end[]; /* link.ld */

/* From the 0x37df4 stub: end of the VERSION.TXT writer on the normal mount
 * branch, storage task (task_storage_service_entry), 4 KB stack. Runs on
 * every mount and every re-insert. */
void load_settings(void)
{
	static u8 grown;
	char *text;
	const char *how;
	s32 n;

	/* stock just mounted the card: its cache is fresh, stop any remount retry */
	st.card_stale = 0;
	led_init();
	cfg_defaults();
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
		cfg_parse(text, n);
		fw_free(text);
	}

	cfg_apply();

	/* cfg: display led sound log, then usb photo video meter */
	v5_log("v5 %u B, C100.TXT %s", (u32)__v5_end - (u32)__v5_start, how);
	v5_log("cfg %u%u%u%u usb %u%u%u", cfg.display, cfg.led, cfg.sound, cfg.log,
	       cfg.usb[POS_PHOTO], cfg.usb[POS_VIDEO], cfg.usb[POS_METER]);
	/* arms every hook (stubs.S); never reached on a boot that flashes */
	st.booted = 1;
}
