#include "v5.h"
#include "fw.h"

#define LOG_EVERY 2000 /* ms */
#define HDR 128        /* longest header is under 80 chars */

/* Byte ring fed from any task, drained by the tick, IRQs masked around each
 * access. load_settings moves it to the heap before its first line, so the
 * static buffer is used only when that malloc fails. */

static char early[64];
static char *ring = early;
static u32 size = sizeof(early);
static u32 head, tail;   /* free-running, index = x & (size - 1) */
static u32 dropped;

/* the patch's own lines always go in the ring; log_flush writes nothing unless cfg.log */
void log_put(const char *s, u32 n)
{
	u32 c;

	c = v5_irq_save();
	while (n--) {
		if (head - tail == size) {
			tail++;
			dropped++;
		}
		ring[head++ & (size - 1)] = *s++;
	}
	v5_irq_restore(c);
}

u32 log_pending(void)
{
	return head - tail;
}

u32 log_take(char *dst, u32 max)
{
	u32 c, n = 0;

	c = v5_irq_save();
	while (n < max && tail != head)
		dst[n++] = ring[tail++ & (size - 1)];
	v5_irq_restore(c);
	return n;
}

u32 log_dropped(void)
{
	u32 c, d;

	c = v5_irq_save();
	d = dropped;
	dropped = 0;
	v5_irq_restore(c);
	return d;
}

/* newsize must be a power of two */
void log_grow(char *buf, u32 newsize)
{
	u32 c, n = 0;

	c = v5_irq_save();
	while (tail != head && n < newsize)
		buf[n++] = ring[tail++ & (size - 1)];
	ring = buf;
	size = newsize;
	tail = 0;
	head = n;
	v5_irq_restore(c);
}

/* Every stock printf line, from the 0x535e0 stub, any task, once armed:
 * lines printed before C100.TXT is read go to the UART only. IRQs stay masked
 * for the whole copy because PRINT_BUF has no lock and another task's printf
 * would rewrite it under us. Nothing in the masked copy prints, so it cannot
 * nest. */
void logcap(const char *line)
{
	u32 c, n;

	if (!cfg.log)
		return;
	c = v5_irq_save();
	for (n = 0; n < 511 && line[n]; n++)
		;
	log_put(line, n);
	v5_irq_restore(c);
}

/* Drain the ring to LOG.TXT in one append. Unthrottled, so act_reboot gets a
 * real flush; the tick goes through log_poll. Peripheral task only, and
 * nothing below calls it, so it never re-enters. Stock file calls print on
 * their error paths; those lines go to the ring and out with the next flush. */
void log_flush(void)
{
	s32 t[4];
	u32 n, h, d;
	char *buf;

	n = log_pending();
	if (n && cfg.log && fs_ready() && (buf = fw_malloc(HDR + n))) {
		t[0] = t[1] = t[2] = t[3] = 0; /* left as is when the RTC read fails */
		fw_rtc_read(t);
		d = log_dropped();
		/* ms since boot, RTC day and time */
		h = v5_fmt(buf, HDR, "== %u %ud %02u:%02u:%02u", v5_ms(), t[3], t[2], t[1], t[0]);
		if (d)
			h += v5_fmt(buf + h, HDR - h, " lost %u", d);
		buf[h++] = '\r';
		buf[h++] = '\n';
		n = log_take(buf + h, n);
		fs_append(P_LOG, buf, h + n);
		fs_flush();
		fw_free(buf);
	}
}

/* from the tick: every LOG_EVERY ms, sooner once the ring is half full */
void log_poll(void)
{
	if (log_pending() > size / 2 || (s32)(v5_ms() - st.next_log_ms) >= 0) {
		st.next_log_ms = v5_ms() + LOG_EVERY;
		log_flush();
	}
}
