#include "v5.h"
#include "fw.h"

/* CMD.TXT `webcam [MIN]` (docs/usb.md): one boot as the webcam whatever the
 * switch, then, with MIN, a reboot MIN minutes later that follows the switch
 * again. The command writes a marker file holding MIN and reboots. On the next
 * boot load_settings deletes the marker first and acts on it only if the
 * delete worked, the same rule as CMD.TXT: a card that keeps the file gives a
 * normal boot, and the reboot at the end finds no marker, so it cannot loop.
 * All of it is overlay code that runs after the update check. */

#define P_ONCE "G:\\WEBCAM.ONE"
#define ONCE_MAX 16   /* bytes; a longer file is not ours */
#define MIN_MAX 1440  /* minutes: one day */
#define RETRY 60000   /* ms between reboot tries while one is refused */

u8 webcam_forced;     /* this boot: the webcam on every switch position */
static u32 reboot_at; /* ms, 0 = no timer */

static int space(char c)
{
	return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

/* "0".."1440" with optional spaces and line ends around it, else -1 */
s32 webcam_min(const char *s)
{
	s32 n = 0;
	int digits = 0;

	while (space(*s))
		s++;
	for (; *s >= '0' && *s <= '9'; s++) {
		n = n * 10 + (*s - '0');
		if (n > MIN_MAX)
			return -1;
		digits++;
	}
	while (space(*s))
		s++;
	return digits && !*s ? n : -1;
}

/* From cmd_run (peripheral task, the card is the camera's). Writes the marker,
 * then reboots through act_reboot. Nothing is written when act_reboot would
 * refuse. If act_reboot comes back with the card still mounted (the unmount
 * failed), the marker is deleted again; if it comes back after the unmount
 * (no power cut), the marker stays and the next boot uses it. */
void webcam_cmd(s32 min)
{
	char buf[8];
	s32 fd, len, w;

	if (reboot_busy()) {
		v5_log("reboot %x", 3);
		return;
	}
	len = v5_fmt(buf, sizeof(buf), "%d\r\n", min);
	fd = fw_open(P_ONCE, O_CREATE_TRUNC);
	if (fd < 0) {
		v5_log("webcam: write fail");
		return;
	}
	w = fw_write(fd, buf, len);
	fw_close(fd);
	if (w != len) {
		fs_delete(P_ONCE);
		fs_flush();
		v5_log("webcam: write fail");
		return;
	}
	fs_flush();
	v5_log("webcam %d", min);
	act_reboot();
	if (fs_ready() && !fs_delete(P_ONCE)) {
		fs_flush();
		v5_log("webcam: marker removed");
	}
}

/* From load_settings (storage task, after the update check), first call of
 * each boot only: overlay .bss is zeroed by each load, and the overlay loads
 * once per boot. */
void webcam_boot(void)
{
	static u8 checked;
	char *text;
	s32 n;

	if (checked)
		return;
	checked = 1;
	n = fs_read(P_ONCE, &text, ONCE_MAX);
	if (!text && !n)
		return;
	if (fs_delete(P_ONCE)) {
		v5_log("webcam: rm fail");
		if (text)
			fw_free(text);
		return;
	}
	fs_flush();
	if (!text) {
		v5_log("webcam: bad file");
		return;
	}
	while (n > 0 && space(text[n - 1]))
		text[--n] = 0;
	n = webcam_min(text);
	if (n < 0)
		v5_log("webcam: bad %s", text);
	fw_free(text);
	if (n < 0)
		return;
	webcam_forced = 1;
	if (n)
		reboot_at = (v5_ms() + (u32)n * 60000) | 1;
	v5_log("webcam: boot %d", n);
}

/* every v5_tick pass, peripheral task */
void webcam_tick(void)
{
	if (!reboot_at || (s32)(v5_ms() - reboot_at) < 0)
		return;
	reboot_at = (v5_ms() + RETRY) | 1;
	v5_log("webcam: time up");
	act_reboot();
}
