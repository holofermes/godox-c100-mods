#include "v5.h"
#include "fw.h"

/* Canary (docs/hooks.md): the release stubs and arm gate with every feature cut.
 * The SET hook appends one line to LOG.TXT and never sets st.booted, so the
 * gate stays shut and every hook runs only its displaced stock instruction.
 * The line carries st.sound_pending: 2 or 3 means S1 parked the power-on
 * chime on the way up, proof that a disarmed stub ran on the camera. */

static char line[] = "canary 1 chime ?\r\n";

/* From the 0x37df4 stub, storage task, after the updater check on every
 * mount. The fs lock is free there. */
void load_settings(void)
{
	s32 fd;

	line[sizeof(line) - 4] = '0' + st.sound_pending % 10;
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

/* G6 replaces the eject callback pool word, so this runs armed or not. */
u32 v5_eject_cb(u32 lun)
{
	return fw_stock_eject_cb(lun);
}

/* The rest are reached only through an armed stub, and the canary never arms.
 * Where it is one call, they do what stock does at the site. */
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
