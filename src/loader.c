#include "v5.h"
#include "fw.h"

/* The part of the C that lives in the RAM gap (src/link.ld): the state the
 * stubs read before the gate opens, and the overlay loader. Everything else
 * is overlay code, linked at 0xff0010 and read from NOR here (docs/hooks.md).
 *
 * The image carries the overlay at file offset OVL_FLASH, in the zero padding
 * between the app (ends at file 0xf6b68) and the resources (0x170000), past
 * what the boot block copies (file 0xf8200 at load length 0x741). The updater
 * writes the file to NOR byte for byte, so NOR offset = file offset. Layout,
 * in NOR and in RAM alike: a 16 B header at 0xff0000, the body from 0xff0010.
 * The overlay .bss follows the body in RAM only. */

#define OVL_FLASH 0x100000
#define OVL_MAGIC 0x4c564f43 /* "COVL" */
#define PAGE 0x100           /* fw_nv_read_page reads 256 B */

struct ovl_hdr {
	u32 magic;
	u32 len; /* body bytes: overlay code, rodata and data */
	u32 crc; /* CRC-32 (zlib) of the body */
	u32 pad;
};

extern u32 __ovl_base[], __ovl_start[], __ovl_bss_start[], __ovl_end[]; /* link.ld, word aligned */

/* The overlay this gap belongs to: body length and CRC-32. tools/patch.py
 * writes them over the placeholder after linking, from the same build's
 * overlay, so a gap from one build never runs another build's overlay (for
 * example after a flash cut between erase blocks 15 and 16). volatile: the
 * compiler must read the words patch.py writes, not fold the placeholder. */
const volatile u32 ovl_expect[2] = { 0xffffffff, 0xffffffff };

/* stubs.S reads st.booted (the arm flag) and writes st.sound_pending */
struct v5_state st;
_Static_assert(__builtin_offsetof(struct v5_state, sound_pending) == 5, "st.sound_pending");
_Static_assert(__builtin_offsetof(struct v5_state, booted) == 6, "st.booted");
_Static_assert(sizeof(struct ovl_hdr) == 0x10, "header size, link.ld");

static u32 crc32(const u8 *p, u32 n)
{
	u32 c = 0xffffffff;
	int k;

	while (n--) {
		c ^= *p++;
		for (k = 0; k < 8; k++)
			c = (c >> 1) ^ (0xedb88320 & -(c & 1));
	}
	return ~c;
}

/* NOR [OVL_FLASH + from, OVL_FLASH + to) into the same offsets from 0xff0000,
 * whole pages; the last one runs into the .bss, zeroed after. */
static int read_pages(u32 from, u32 to)
{
	for (; from < to; from += PAGE)
		if (fw_nv_read_page(OVL_FLASH + from, (u8 *)__ovl_base + from))
			return 0;
	return 1;
}

/* 0 = loaded. Else the reason, kept in st.load_err: 1 NOR reader not up yet,
 * 2 read failed, 3 magic, 4 length, 5 body CRC, 6 another build's overlay. */
static u8 ovl_load(void)
{
	const struct ovl_hdr *h = (const struct ovl_hdr *)__ovl_base;
	u32 len = ovl_expect[0];
	u32 *p;

	/* nv init (state_handling task) creates the mutex, starts the SPI
	 * controller, then reads the resource directory into W_NV_DIR */
	if (!W_NV_MUTEX || !W_NV_DIR)
		return 1;
	if (!read_pages(0, PAGE))
		return 2;
	if (h->magic != OVL_MAGIC)
		return 3;
	/* this build's body: the bound on the reads, then its CRC */
	if (h->len != len)
		return 4;
	if (h->crc != ovl_expect[1])
		return 6;
	if (!read_pages(PAGE, sizeof(*h) + len))
		return 2;
	if (crc32((const u8 *)__ovl_start, len) != h->crc)
		return 5;
	for (p = __ovl_bss_start; p < __ovl_end; p++)
		*p = 0;
	/* nv_read_page left the pages in RAM, not in the D-cache; the .bss
	 * stores may still sit there. Code is fetched through the I-cache,
	 * which does not see the D-cache. */
	fw_dcache_clean(__ovl_base, (u8 *)__ovl_end - (u8 *)__ovl_base);
	v5_icache_inval();
	return 0;
}

/* A failed load is otherwise silent on the camera: one line per failed
 * mount, whatever the log setting (settings are not loaded then). Same calls
 * as the canary's line writer: the stock file wrappers take and give the fs
 * mutex themselves, and the storage task holds no fs lock at 0x37fa0. */
static char err_line[] = "ovl err ?\r\n";

static void log_err(u8 e)
{
	s32 fd;

	err_line[8] = '0' + e;
	fd = fw_open(P_LOG, O_READ_WRITE);
	if (fd >= 0)
		fw_lseek(fd, 0, 2);
	else
		fd = fw_open(P_LOG, O_CREATE_TRUNC);
	if (fd < 0)
		return;
	fw_write(fd, err_line, sizeof(err_line) - 1);
	fw_close(fd);
	fw_flush(DRIVE_G);
}

/* From the SET stub at 0x37fa0: end of the VERSION.TXT writer on the normal
 * mount branch, storage task (4 KB stack), after the card updater check, on
 * every fresh mount (power-on, card insert; the 0x5001 re-polls with the card
 * still in stop at 0x3dd18 before the update check). Loads the overlay once
 * per boot: on the first mount of a boot ([0x123d34] is still 0 there, crt0
 * zeroed it) or on a later fresh mount if no load has worked yet. A failed
 * load leaves the gate shut and the camera runs as the base image. */
void v5_set(void)
{
	/* the updater started on this boot: a card re-insert during the update
	 * window reaches SET too (0x3dfa0 only takes the update branch at 0) */
	if (B_UPD_CHECKED == 2)
		return;
	if (!st.loaded || B_UPD_CHECKED == 0) {
		st.booted = 0;
		st.loaded = 0;
		__asm__ volatile("" ::: "memory");
		st.load_err = ovl_load();
		if (st.load_err) {
			log_err(st.load_err);
			return;
		}
		st.loaded = 1;
	}
	load_settings(); /* the overlay; it sets st.booted at its end */
}
