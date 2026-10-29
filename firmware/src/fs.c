#include "v5.h"
#include "fw.h"

/* fw_stat fills 0x14 bytes: attr u16 at +0, size u64 at +8 stored with STRD
 * (needs 8-byte alignment on ARMv5TE), time at +0x10. Align by hand so the
 * caller's sp does not matter. -1 when absent, size clamped to 31 bits. */
s32 fs_size(const char *path)
{
	u32 raw[8], a = (u32)raw;
	u32 *sb;

	/* GCC assumes an 8-aligned sp and would drop the rounding */
	__asm__("" : "+r"(a));
	sb = (u32 *)((a + 7) & ~7u);
	if (fw_stat(path, sb) != 0)
		return -1;
	if (sb[3] || (s32)sb[2] < 0)
		return 0x7fffffff;
	return sb[2];
}

/* The camera owns the card: mounted, no mount failure reported, no remount
 * pending, no eject in progress or waiting to be serviced, and the host is not
 * holding it as a drive (LUN 0 status 0xffaa means the host ejected it). */
int fs_ready(void)
{
	if (B_CARD_OK != 1 || B_MOUNT_FAIL || st.card_stale || st.host_released || st.ejecting)
		return 0;
	if (st.usb_active == USB_DRIVE && H_LUN0 != LUN_EJECTED)
		return 0;
	return 1;
}

/* stat does the existence open itself (0xa5318 mode 0x100), so probing with it
 * keeps a single handle on the file at a time */
s32 fs_read(const char *path, char **buf, u32 max)
{
	s32 size, fd, n;
	char *p;

	*buf = NULL;
	size = fs_size(path);
	if (size < 0)
		return 0;
	if ((u32)size > max)
		return -1;
	p = fw_malloc(size + 1);
	if (!p)
		return -1;
	fd = fw_open(path, O_READ_ONLY);
	if (fd < 0) {
		fw_free(p);
		return -1;
	}
	n = fw_read(fd, p, size);
	fw_close(fd);
	if (n < 0) {
		fw_free(p);
		return -1;
	}
	p[n] = 0;
	*buf = p;
	return n;
}

/* O_READ_WRITE opens an existing file without truncating, at position 0 */
void fs_append(const char *path, const char *data, u32 n)
{
	s32 fd = fw_open(path, O_READ_WRITE);

	if (fd >= 0)
		fw_lseek(fd, 0, 2);
	else
		fd = fw_open(path, O_CREATE_TRUNC);
	if (fd < 0)
		return;
	fw_write(fd, data, n);
	fw_close(fd);
}

s32 fs_delete(const char *path)
{
	return fw_unlink(path);
}

void fs_flush(void)
{
	fw_flush(DRIVE_G);
}
