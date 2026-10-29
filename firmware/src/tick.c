#include "v5.h"
#include "fw.h"
#include "xu.h"

#define CMD_MAX 2048
#define CMD_EVERY 3000   /* ms */
#define STALE_WAIT 1000  /* lets an SD uninit posted to the USB task land first */
#define STALE_RETRY 5000
#define STALE_TRIES 6    /* then leave it to stock's next mount, which clears card_stale */

static u32 stale_at;     /* remount due, 0 = none pending */
static u8 stale_tries;
static u8 mount_failed;  /* the last remount got past SD init and failed in mount */

/* xTaskGetTickCount; 1 tick = 1 ms (0x5c110 converts ms*1000/1000).
 * Wraps after 49 days: compare with a signed difference, as due() does. */
u32 v5_ms(void)
{
	return fw_ticks();
}

static int due(u32 t)
{
	return (s32)(v5_ms() - t) >= 0;
}

/* One attempt of the stock mount 0x3da8c per call: fs take, SD init, fs give
 * (0x3dac8..0x3db7c), mount (0x3db84). A failure gets stock's recovery before
 * the next attempt: SD uninit after a failed SD init (0x3dd3c), unmount and SD
 * uninit after a failed mount (0x3dba4..0x3dbb0), and stock then retries the
 * mount alone (0x3dbc0) with no SD init of its own; the SD0 drive table at
 * 0xe4978 has an init op, 0x9b570, that runs SD init. Stock waits 100 ms
 * between attempts, the tick 5 s. mount drops the drive's cached sectors, so
 * what the host wrote is read fresh. */
static int remount(void)
{
	s32 r;

	if (!mount_failed) {
		fw_fs_take();
		r = fw_sd_init(0);
		fw_fs_give();
		if (r) {
			v5_log("remount sd %d", r);
			fw_sd_uninit(0);
			return 0;
		}
	}
	r = fw_mount(DRIVE_G);
	v5_log("remount %d", r);
	mount_failed = r != 0;
	if (r) {
		fw_unmount(DRIVE_G);
		fw_sd_uninit(0);
		return 0;
	}
	B_CARD_OK = 1;
	st.card_stale = 0;
	return 1;
}

/* a firmware update on the card flashes at the next boot; no command runs over it */
static int update_pending(void)
{
	s32 fd = fw_open(P_FW, O_READ_ONLY);

	if (fd < 0)
		return 0;
	fw_close(fd);
	return 1;
}

static void cmd_poll(void)
{
	char *text;
	s32 n;

	if (!fs_ready() || update_pending())
		return;
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

/* The line a host sent through the webcam's extension unit (xu.S), run like a
 * CMD.TXT line. Taken once the card is the camera's, so the update check and
 * the commands that write to the card see it; until then it waits in the
 * mailbox and the host sees it there (GET_CUR). With an update on the card it
 * is taken and dropped. */
static void xu_poll(void)
{
	char line[XU_LEN + 1];
	u32 c, i;

	if (!xu_full || !fs_ready())
		return;
	c = v5_irq_save();
	for (i = 0; i < XU_LEN; i++) {
		line[i] = xu_box[i];
		xu_box[i] = 0;
	}
	xu_full = 0;
	v5_irq_restore(c);
	line[XU_LEN] = 0;
	for (i = 0; line[i]; i++)
		if (line[i] == '\r' || line[i] == '\n')
			line[i] = 0;
	if (update_pending()) {
		v5_log("xu: %s not run, update on the card", line);
		return;
	}
	v5_log("xu: %s", line);
	cmd_line(line);
}

/* From the 0x22360 stub in the peripheral task (4 KB stack), every ~250 ms,
 * after the stub ran fw_seg_refresh. */
void v5_tick(void)
{
	log_poll();
	if (due(st.next_cmd_ms)) {
		st.next_cmd_ms = v5_ms() + CMD_EVERY;
		cmd_poll();
	}
	xu_poll();

	/* both off unless both are already off */
	if (st.toggle_req) {
		st.toggle_req = 0;
		cfg.display = cfg.led = !(cfg.display || cfg.led);
		cfg_apply();
	}

	act_tick(); /* LCD, LED, late chime, drive handback */
	webcam_tick(); /* the reboot MIN minutes after a `webcam MIN` boot */

	/* The eject callback ran the SD uninit 0xb3844, so the card is re-inited
	 * before anyone touches it. While the LUN still reads 0xffaa the host
	 * sees no medium: catch up on CMD.TXT and LOG.TXT, then let act.c hand
	 * the card back. */
	if (st.host_released) {
		st.host_released = 0;
		mount_failed = 0; /* the eject's SD uninit: start from SD init */
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
		mount_failed = 0;
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
