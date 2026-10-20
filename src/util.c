#include <stdarg.h>
#include "v5.h"
#include "fw.h"

/* The firmware's own libc, through its ARM thunks. */
u32 v5_strlen(const char *s)
{
	return fw_strlen(s);
}

void v5_memcpy(void *d, const void *s, u32 n)
{
	fw_memcpy(d, s, n);
}

/* The firmware carries newlib vsnprintf (ARM thunk 0xce11c, Thumb body
 * 0xcab9c, full specifier set), the same routine its own printf uses at
 * 0x53584. */
int v5_fmt(char *buf, int size, const char *fmt, ...)
{
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = fw_vsnprintf(buf, (u32)size, fmt, ap);
	va_end(ap);
	return n; /* characters the line wanted, as newlib reports it */
}

void v5_log(const char *fmt, ...)
{
	char line[128];
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = fw_vsnprintf(line, sizeof(line), fmt, ap);
	va_end(ap);
	if (n < 0)
		return;
	if (n > (int)sizeof(line) - 1)
		n = sizeof(line) - 1;
	log_put("[v5] ", 5);
	log_put(line, (u32)n);
	log_put("\r\n", 2);
}

static char lower(char c)
{
	return (c >= 'A' && c <= 'Z') ? c + 32 : c;
}

/* case-insensitive */
int v5_streq(const char *a, const char *b)
{
	while (*a && lower(*a) == lower(*b)) {
		a++;
		b++;
	}
	return *a == 0 && *b == 0;
}
