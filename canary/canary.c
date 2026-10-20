#include "v5.h"
#include "fw.h"

/* Canary (docs/hooks.md): the release's boot-time layer (stubs, gate, heap
 * reservation, loader) with every feature cut. This file is the whole
 * overlay. The loader reads it from NOR on the first card mount of a boot, or
 * a later one if that load failed, and calls load_settings, which appends one
 * line to LOG.TXT and never sets st.booted, so the gate stays shut and every
 * hook runs only its displaced stock instruction. The line carries:
 *   - the CRC-32 the loader checked, from the header at 0xff0000 (build.sh
 *     prints the build's): the overlay that ran is the one in this image;
 *   - st.sound_pending: 2 means S1 held the startup chime (the only id 2
 *     player in V1.1.6) on the way up, proof that a disarmed stub ran on the
 *     camera. A failed load writes `ovl err N` instead (loader.c). */

extern u32 __ovl_base[]; /* link.ld: the header, magic len crc pad */

static char line[] = "canary 2 ovl ........ chime ?\r\n";

static void hex8(char *d, u32 v)
{
	int i;

	for (i = 7; i >= 0; i--, v >>= 4)
		d[i] = "0123456789abcdef"[v & 15];
}

/* From v5_set, storage task, after the updater check on every mount. The fs
 * lock is free there. */
void load_settings(void)
{
	s32 fd;

	hex8(line + 13, __ovl_base[2]);
	line[sizeof(line) - 4] = st.sound_pending < 10 ? '0' + st.sound_pending : '?';
	fd = fw_open(P_LOG, O_READ_WRITE);
	if (fd >= 0)
		fw_lseek(fd, 0, 2);
	else
		fd = fw_open(P_LOG, O_CREATE_TRUNC);
	if (fd < 0)
		return;
	fw_write(fd, line, sizeof(line) - 1);
	fw_close(fd);
	fw_flush(DRIVE_G);
}

/* The rest are reached only through an armed stub, and the canary never arms.
 * Where it is one call, they do what stock does at the site. */
u32 v5_eject_cb(u32 lun)
{
	return fw_stock_eject_cb(lun);
}

void logcap(const char *line)
{
	(void)line;
}

void v5_tick(void)
{
}

void v5_usb_plugin(void)
{
}

void v5_usb_unplug(void)
{
	fw_usb_hw_off();
}

void v5_usb_tick(void)
{
}

void act_long_select(void)
{
}

s32 act_sound_gate(s32 id)
{
	(void)id;
	return 1;
}
