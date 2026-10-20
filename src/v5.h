#ifndef V5_H
#define V5_H

/* The patch's features, in the unused RAM gap of the Godox C100 firmware (docs/hooks.md).
 * Shared declarations; stock addresses live in fw.h. */

typedef unsigned int u32;
typedef int s32;
typedef unsigned short u16;
typedef unsigned char u8;

#define NULL ((void *)0)

/* ---- shared enums ---- */
enum { USB_NONE = 0, USB_DRIVE = 1, USB_WEBCAM = 2 };
enum { POS_PHOTO = 0, POS_VIDEO = 1, POS_METER = 2 };
#define NPOS 3

struct v5_cfg {
	u8 display; /* 1 = segments shown */
	u8 led;     /* 1 = LED as stock */
	u8 sound;   /* 1 = sounds as stock */
	u8 log;     /* 1 = write LOG.TXT (the patch's lines and the firmware's prints) */
	u8 usb[NPOS];
	u8 led_off43; /* level that turns GPIO 43 off */
	u8 led_off95; /* level that turns GPIO 95 off */
};
extern struct v5_cfg cfg;

/* LED gate state, read by the gpio_set stub in asm: keep the field order. */
struct v5_led {
	u8 off;    /* 1 = force the off levels on 43 and 95 */
	u8 want43; /* last level stock asked for */
	u8 want95;
	u8 off43; /* level that means off, from cfg */
	u8 off95;
};
extern struct v5_led led;

/* misc state, owned by state.c */
struct v5_state {
	u8 usb_active;    /* what is posted now: USB_NONE / USB_DRIVE / USB_WEBCAM */
	u8 usb_pending;   /* wanted personality, deferred because the bus is busy */
	u8 host_released; /* set by v5_eject_cb: the host gave the card back */
	u8 card_stale;    /* camera FS must remount after an SD uninit */
	u8 toggle_req;    /* set by the long-press stub, applied by the tick */
	u8 sound_pending; /* power-on chime id to replay once C100.TXT is known, else 0 */
	u8 booted;        /* settings have been loaded */
	u8 ejecting;      /* v5_eject_cb is inside the stock uninit */
	u32 next_cmd_ms; /* CMD.TXT poll throttle */
	u32 next_log_ms; /* log_poll throttle, log_flush ignores it */
};
extern struct v5_state st;

/* ---- util.c ---- */
u32 v5_strlen(const char *s);
void v5_memcpy(void *d, const void *s, u32 n);
int v5_streq(const char *a, const char *b); /* case-insensitive, exact match */
int v5_fmt(char *buf, int size, const char *fmt, ...); /* newlib vsnprintf */
void v5_log(const char *fmt, ...);

/* ---- log.c ---- */
void log_put(const char *s, u32 n); /* into the ring; cfg.log gates the write to LOG.TXT */
u32 log_pending(void);
u32 log_take(char *dst, u32 max); /* pops at most max bytes */
u32 log_dropped(void);            /* read and clear */
void log_grow(char *buf, u32 newsize); /* power of two, from fw_malloc */
void log_poll(void);              /* throttled flush, from the tick */
void logcap(const char *line);    /* from the 0x535e0 stub: append one printf line */
void log_flush(void);             /* unthrottled: drain the ring to LOG.TXT and flush */

/* ---- cfg.c ---- */
void cfg_defaults(void);
void cfg_parse(char *text, u32 len); /* text[len] must be 0, modified in place */

/* ---- cmd.c ---- */
void cmd_run(char *text, u32 len); /* text[len] must be 0 */

/* ---- state.c ---- */
void led_init(void); /* baked-in LED levels, call before cfg_apply */

/* ---- fs.c ---- */
/* Whole small file into a fw_malloc'd buffer, NUL-terminated, bytes read
 * returned, 0 with *buf NULL when absent, -1 on error or over max. */
s32 fs_read(const char *path, char **buf, u32 max);
void fs_append(const char *path, const char *data, u32 n); /* create if absent */
s32 fs_delete(const char *path);
void fs_flush(void); /* 0x9aa98(DRIVE_G) */
s32 fs_size(const char *path); /* -1 absent, else size clamped to 31 bits */
int fs_ready(void);            /* the camera may use the card now */

/* ---- settings.c ---- */
void load_settings(void); /* from the 0x37df4 mount hook: read C100.TXT, apply */

/* ---- tick.c ---- */
void v5_tick(void); /* from the 0x22238 hook, after fw_seg_refresh() */
u32 v5_ms(void);    /* fw_ticks(), 1 ms; compare with a signed difference */

/* ---- usb.c ---- */
void v5_usb_plugin(void); /* from the 0x1f540 stub */
void v5_usb_unplug(void); /* from the 0x1f4c0 stub; returns without calling
			   * 0xbde60 when it posted an uninit itself */
void v5_usb_tick(void);   /* from the 0x22354 stub, after fw_usb_vbus_tick() */
u32 v5_eject_cb(u32 lun); /* replaces the pool word at 0xe0848 */
void usb_apply(void);     /* act on cfg.usb[] changes from the tick */

/* ---- act.c ---- */
void cfg_apply(void); /* push cfg into the hardware: display, LED, sound, log */
void act_display(u8 on);
void act_led(u8 on);
void act_sound(u8 on);
void act_reboot(void); /* returns only when refused, or still powered after 3 s */
s32 act_sound_gate(s32 id);    /* 1 = let stock play, 0 = return -1 like stock invalid id */
void act_long_select(void);    /* the C side of the K1 gesture */
void act_tick(void);           /* from v5_tick every pass: LCD, LED, chime, handback */
void act_host_released(void);  /* start the drive handback; act_tick writes LUN 0 once idle */

/* ---- stubs.S ---- */
u32 v5_irq_save(void);
void v5_irq_restore(u32 cpsr);

#endif /* V5_H */
