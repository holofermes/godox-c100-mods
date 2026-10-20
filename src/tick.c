#include "v5.h"
#include "fw.h"

#define CMD_MAX 2048
#define CMD_EVERY 3000   /* ms */
#define STALE_WAIT 1000  /* lets an SD uninit posted to the USB task land first */
#define STALE_RETRY 5000
#define STALE_TRIES 6    /* then leave it to stock's next mount, which clears card_stale */

static u32 stale_at;     /* remount due, 0 = none pending */
static u8 stale_tries;

/* xTaskGetTickCount; 1 tick = 1 ms (0x5bf3c converts ms*1000/1000).
 * Wraps after 49 days: compare with a signed difference, as due() does. */
u32 v5_ms(void)
{
	return fw_ticks();
}

static int due(u32 t)
{
	return (s32)(v5_ms() - t) >= 0;
}

/* same calls and order as the stock mount 0x3d92c..0x3db48; mount drops the
 * drive's cached sectors, so what the host wrote is read fresh */
static int remount(void)
{
	s32 r;

	fw_fs_take();
	r = fw_sd_init(0);
	fw_fs_give();
	if (r == 0)
		r = fw_mount(DRIVE_G);
	v5_log("remount %d", r);
	if (r)
		return 0;
	B_CARD_OK = 1;
	st.card_stale = 0;
	return 1;
}

static void cmd_poll(void)
{
	char *text;
	s32 fd, n;

	if (!fs_ready())
		return;
	/* a pending update flashes at the next boot; do not run commands over it */
	fd = fw_open(P_FW, O_READ_ONLY);
	if (fd >= 0) {
		fw_close(fd);
		return;
	}
	n = fs_read(P_CMD, &text, CMD_MAX);
	if (!text && !n)
		return;
	/* off the card before anything in it can hang or reboot the camera; a
	 * file that stays would run again on every poll */
	if (fs_delete(P_CMD)) {
		v5_log("cmd: rm fail");
		if (text)
			fw_free(text);
		return;
	}
	fs_flush();
	if (!text) {
		v5_log("cmd: bad file");
		return;
	}
	v5_log("cmd: %d B", n);
	cmd_run(text, n);
	fw_free(text);
}

/* From the 0x22238 stub in the peripheral task (4 KB stack), every ~250 ms,
 * after the stub ran fw_seg_refresh. */
void v5_tick(void)
{
	log_poll();
	if (due(st.next_cmd_ms)) {
		st.next_cmd_ms = v5_ms() + CMD_EVERY;
		cmd_poll();
	}

	/* both off unless both are already off */
	if (st.toggle_req) {
		st.toggle_req = 0;
		cfg.display = cfg.led = !(cfg.display || cfg.led);
		cfg_apply();
	}

	act_tick(); /* LCD, LED, late chime, drive handback */

	/* The eject callback ran the SD uninit 0xb37a0, so the card is re-inited
	 * before anyone touches it. While the LUN still reads 0xffaa the host
	 * sees no medium: catch up on CMD.TXT and LOG.TXT, then let act.c hand
	 * the card back. */
	if (st.host_released) {
		st.host_released = 0;
		if (remount()) {
			cmd_poll();
			log_flush();
		} else {
			st.card_stale = 1;
		}
		act_host_released();
	}

	if (!st.card_stale) {
		stale_at = 0;
	} else if (!stale_at) {
		stale_at = (v5_ms() + STALE_WAIT) | 1;
		stale_tries = 0;
	} else if (due(stale_at) && stale_tries < STALE_TRIES) {
		stale_at = (v5_ms() + STALE_RETRY) | 1;
		/* a remount drops the drive's cached sectors: never under a
		 * capture or a recording */
		if (!B_CAPTURE_BUSY && !(B_REC_FLAGS & 0xa) && !remount())
			stale_tries++;
	}

	usb_apply();
}
