#include "v5.h"
#include "fw.h"

/* `webcam [MIN]` and `drive` (docs/usb.md): one boot as the webcam or as the
 * USB drive whatever the switch; with MIN, a reboot MIN minutes after the
 * webcam boot that follows the switch again. The command writes a marker file
 * (WEBCAM.ONE holding MIN, DRIVE.ONE) and reboots. On the next boot
 * load_settings deletes the marker first and acts on it only if the delete
 * worked, the same rule as CMD.TXT: a card that keeps the file gives a normal
 * boot, and the reboot at the end finds no marker, so it cannot loop. Both
 * markers at once: both are deleted and the drive wins. All of it is overlay
 * code that runs after the update check. */

#define P_ONCE "G:\\WEBCAM.ONE"
#define P_DRIVE "G:\\DRIVE.ONE"
#define ONCE_MAX 16   /* bytes; a longer file is not ours */
#define MIN_MAX 1440  /* minutes: one day */
#define RETRY 60000   /* ms between reboot tries while one is refused */

u8 webcam_forced;     /* this boot: the webcam on every switch position */
u8 drive_forced;      /* this boot: the drive on every switch position */
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

/* Create the marker holding text and flush. 0 = written; else nothing of it
 * is left on the card and "<tag>: write fail" is logged. */
static int once_write(const char *path, const char *tag, const char *text, s32 len)
{
	s32 fd, w;

	fd = fw_open(path, O_CREATE_TRUNC);
	if (fd < 0) {
		v5_log("%s: write fail", tag);
		return -1;
	}
	w = fw_write(fd, text, len);
	fw_close(fd);
	if (w != len) {
		fs_delete(path);
		fs_flush();
		v5_log("%s: write fail", tag);
		return -1;
	}
	fs_flush();
	return 0;
}

/* Reboot through act_reboot. If it comes back with the card still mounted
 * (the unmount failed), the marker is deleted again; if it comes back after
 * the unmount (no power cut), the marker stays and the next boot uses it. */
static void once_reboot(const char *path, const char *tag)
{
	act_reboot();
	if (fs_ready() && !fs_delete(path)) {
		fs_flush();
		v5_log("%s: marker removed", tag);
	}
}

/* From cmd_line (peripheral task, the card is the camera's). Nothing is
 * written when act_reboot would refuse. */
void webcam_cmd(s32 min)
{
	char buf[8];
	s32 len;

	if (reboot_busy()) {
		v5_log("reboot %x", 3);
		return;
	}
	len = v5_fmt(buf, sizeof(buf), "%d\r\n", min);
	if (once_write(P_ONCE, "webcam", buf, len))
		return;
	v5_log("webcam %d", min);
	once_reboot(P_ONCE, "webcam");
}

/* the same for `drive`; the marker's text is not read back */
void drive_cmd(void)
{
	static const char text[] = "drive\r\n";

	if (reboot_busy()) {
		v5_log("reboot %x", 3);
		return;
	}
	if (once_write(P_DRIVE, "drive", text, sizeof(text) - 1))
		return;
	v5_log("drive");
	once_reboot(P_DRIVE, "drive");
}

/* Read a marker of at most ONCE_MAX bytes and delete it, then flush. The
 * text (fw_malloc'd, trailing spaces cut) only if the delete worked and the
 * file was readable, else NULL; absent is silent, the rest is logged. */
static char *once_take(const char *path, const char *tag)
{
	char *text;
	s32 n;

	n = fs_read(path, &text, ONCE_MAX);
	if (!text && !n)
		return NULL;
	if (fs_delete(path)) {
		v5_log("%s: rm fail", tag);
		if (text)
			fw_free(text);
		return NULL;
	}
	fs_flush();
	if (!text) {
		v5_log("%s: bad file", tag);
		return NULL;
	}
	while (n > 0 && space(text[n - 1]))
		text[--n] = 0;
	return text;
}

/* From load_settings (storage task, after the update check), first call of
 * each boot only: overlay .bss is zeroed by each load, and the overlay loads
 * once per boot. */
void once_boot(void)
{
	static u8 checked;
	char *text;
	s32 n;

	if (checked)
		return;
	checked = 1;
	text = once_take(P_DRIVE, "drive");
	if (text) {
		fw_free(text);
		drive_forced = 1;
		v5_log("drive: boot");
	}
	text = once_take(P_ONCE, "webcam");
	if (!text)
		return;
	n = webcam_min(text);
	if (n < 0)
		v5_log("webcam: bad %s", text);
	fw_free(text);
	if (n < 0)
		return;
	if (drive_forced) {
		v5_log("webcam: ignored, drive boot");
		return;
	}
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
