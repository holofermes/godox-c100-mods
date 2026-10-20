#include "v5.h"

/* CMD.TXT: one command per line, results go to LOG.TXT when log=on.
 *   reboot                  RTC-alarm reboot after the log is flushed
 *   display on|off, led on|off, sound on|off
 *   set KEY=VALUE           same as a C100.TXT line, applied now
 */

#define MAXTOK 6

static int split(char *s, char **tok)
{
	int n = 0;

	while (*s && n < MAXTOK) {
		while (*s == ' ' || *s == '\t' || *s == '\r')
			*s++ = 0;
		if (!*s)
			break;
		tok[n++] = s;
		while (*s && *s != ' ' && *s != '\t' && *s != '\r')
			s++;
	}
	return n;
}

static void run_line(char *line)
{
	char *t[MAXTOK];
	int n;

	n = split(line, t);
	if (!n || t[0][0] == '#')
		return;

	if (v5_streq(t[0], "reboot")) {
		act_reboot();
	} else if ((v5_streq(t[0], "display") || v5_streq(t[0], "led") ||
		    v5_streq(t[0], "sound")) && n == 2) {
		char kv[32];
		v5_fmt(kv, sizeof(kv), "%s=%s", t[0], t[1]);
		cfg_parse(kv, v5_strlen(kv));
		cfg_apply();
	} else if (v5_streq(t[0], "set") && n == 2) {
		cfg_parse(t[1], v5_strlen(t[1]));
		cfg_apply();
	} else {
		v5_log("cmd: bad %s", t[0]);
	}
}

/* text[len] must be 0 */
void cmd_run(char *text, u32 len)
{
	u32 i = 0;

	while (i < len) {
		char *line = text + i;
		while (i < len && text[i] != '\n')
			i++;
		text[i++] = 0;
		run_line(line);
	}
}
