#include "v5.h"
#include "fw.h"

/* Display, LED and sound on, LOG.TXT off, Photo = drive, Video and Meter =
 * webcam, LED off levels 0 (GPIO 43 is lit at 1, docs/hardware.md). The
 * initializer is the same set as cfg_defaults, so a card-less boot, which
 * never reaches it, is sane too. */
struct v5_cfg cfg = {
	1, 1, 1, 0,
	{ USB_DRIVE, USB_WEBCAM, USB_WEBCAM },
	0, 0,
};

void cfg_defaults(struct v5_cfg *c)
{
	static const u8 dflt[] = { 1, 1, 1, 0, USB_DRIVE, USB_WEBCAM, USB_WEBCAM, 0, 0 };

	_Static_assert(sizeof(dflt) == sizeof(struct v5_cfg), "defaults cover struct v5_cfg");
	v5_memcpy(c, dflt, sizeof(dflt));
}

#define K_BOOL 0
#define K_USB 1

static const struct {
	const char *k;
	u8 off;
	u8 kind;
} keys[] = {
	{ "display", __builtin_offsetof(struct v5_cfg, display), K_BOOL },
	{ "led", __builtin_offsetof(struct v5_cfg, led), K_BOOL },
	{ "sound", __builtin_offsetof(struct v5_cfg, sound), K_BOOL },
	{ "log", __builtin_offsetof(struct v5_cfg, log), K_BOOL },
	{ "led_off43", __builtin_offsetof(struct v5_cfg, led_off43), K_BOOL },
	{ "led_off95", __builtin_offsetof(struct v5_cfg, led_off95), K_BOOL },
	{ "usb_photo", __builtin_offsetof(struct v5_cfg, usb[POS_PHOTO]), K_USB },
	{ "usb_video", __builtin_offsetof(struct v5_cfg, usb[POS_VIDEO]), K_USB },
	{ "usb_meter", __builtin_offsetof(struct v5_cfg, usb[POS_METER]), K_USB },
};

static int set_key(struct v5_cfg *c, const char *k, const char *v)
{
	u32 i;

	for (i = 0; i < sizeof(keys) / sizeof(keys[0]); i++) {
		u8 *dst;
		u32 want;

		if (!v5_streq(k, keys[i].k))
			continue;
		dst = (u8 *)c + keys[i].off;
		if (keys[i].kind == K_USB) {
			if (v5_streq(v, "drive"))
				want = USB_DRIVE;
			else if (v5_streq(v, "webcam"))
				want = USB_WEBCAM;
			else
				return 0;
		} else if (v5_streq(v, "on") || v5_streq(v, "1") || v5_streq(v, "yes")) {
			want = 1;
		} else if (v5_streq(v, "off") || v5_streq(v, "0") || v5_streq(v, "no")) {
			want = 0;
		} else {
			return 0;
		}
		*dst = (u8)want;
		return 1;
	}
	return 0;
}

static char *trim(char *s)
{
	char *e;

	while (*s == ' ' || *s == '\t')
		s++;
	e = s + v5_strlen(s);
	while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r'))
		*--e = 0;
	return s;
}

/* key=value lines into c, '#' comments; text[len] must be 0, modified in place */
void cfg_parse(struct v5_cfg *c, char *text, u32 len)
{
	u32 i = 0;

	while (i < len) {
		char *line = text + i, *eq, *k, *v;
		while (i < len && text[i] != '\n')
			i++;
		text[i++] = 0;   /* i == len hits the caller's terminator */
		line = trim(line);
		if (!*line || *line == '#')
			continue;
		for (eq = line; *eq && *eq != '='; eq++)
			;
		if (!*eq) {
			v5_log("bad %s", line);
			continue;
		}
		*eq = 0;
		k = trim(line);
		v = trim(eq + 1);
		v5_log(set_key(c, k, v) ? "%s=%s" : "bad %s=%s", k, v);
	}
}
