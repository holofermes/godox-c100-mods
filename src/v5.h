#ifndef V5_H
#define V5_H

/* The patch's features for the Godox C100 firmware (docs/hooks.md): the boot-time
 * layer in the unused RAM gap, the features in an overlay read from NOR.
 * Shared declarations; stock addresses live in fw.h. */

/* Calls through a register: gap and overlay are ~15.7 MB apart, past Thumb BL
 * range, and a Thumb BL to ARM code gets a linker veneer. patch.py refuses a
 * link with any veneer. */
#define CROSS __attribute__((long_call))

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

/* misc state, in the gap (src/loader.c): the stubs read booted and write
 * sound_pending before the overlay is loaded */
struct v5_state {
	u8 usb_active;    /* what is posted now: USB_NONE / USB_DRIVE / USB_WEBCAM */
	u8 usb_pending;   /* wanted personality, deferred because the bus is busy */
	u8 host_released; /* set by v5_eject_cb: the host gave the card back */
	u8 card_stale;    /* camera FS must remount after an SD uninit */
	u8 toggle_req;    /* set by the long-press stub, applied by the tick */
	u8 sound_pending; /* power-on chime id to replay once C100.TXT is known, else 0 */
	u8 booted;        /* the arm flag: load_settings ran on this boot's overlay */
	u8 ejecting;      /* v5_eject_cb is inside the stock uninit */
	u32 next_cmd_ms; /* CMD.TXT poll throttle */
	u32 next_log_ms; /* log_poll throttle, log_flush ignores it */
	u8 loaded;       /* the overlay was read and checked on this boot */
	u8 load_err;     /* why the last load failed, 0 = it did not (loader.c) */
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
void logcap(const char *line);    /* from the 0x537b4 stub: append one printf line */
void log_flush(void);             /* unthrottled: drain the ring to LOG.TXT and flush */

/* ---- cfg.c ---- */
void cfg_defaults(struct v5_cfg *c);
void cfg_parse(struct v5_cfg *c, char *text, u32 len); /* text[len] must be 0, modified in place */

/* ---- cmd.c ---- */
void cmd_run(char *text, u32 len); /* CMD.TXT: text[len] must be 0 */
void cmd_line(char *line);         /* one line, NUL-terminated, no '\n' */

/* ---- state.c ---- */
void led_init(void); /* baked-in LED levels, call before cfg_apply */

/* ---- fs.c ---- */
/* Whole small file into a fw_malloc'd buffer, NUL-terminated, bytes read
 * returned, 0 with *buf NULL when absent, -1 on error or over max. */
s32 fs_read(const char *path, char **buf, u32 max);
void fs_append(const char *path, const char *data, u32 n); /* create if absent */
s32 fs_delete(const char *path);
void fs_flush(void); /* 0x9ac6c(DRIVE_G) */
s32 fs_size(const char *path); /* -1 absent, else size clamped to 31 bits */
int fs_ready(void);            /* the camera may use the card now */

/* ---- settings.c ---- */
CROSS void load_settings(void); /* overlay entry, from v5_set: read C100.TXT, apply, arm */

/* ---- loader.c (gap) ---- */
void v5_set(void); /* from the 0x37fa0 mount hook: load the overlay once, then load_settings */

/* ---- tick.c ---- */
void v5_tick(void); /* from the 0x22360 hook, after fw_seg_refresh() */
u32 v5_ms(void);    /* fw_ticks(), 1 ms; compare with a signed difference */

/* ---- usb.c ---- */
void v5_usb_plugin(void); /* from the 0x1f5b0 stub */
void v5_usb_unplug(void); /* from the 0x1f530 stub; returns without calling
			   * 0xbdf04 when it posted an uninit itself */
void v5_usb_tick(void);   /* from the 0x2247c stub, after fw_usb_vbus_tick() */
u32 v5_eject_cb(u32 lun); /* from eject_stub (the word at 0xe0978), armed only */
void usb_apply(void);     /* act on cfg.usb[] changes from the tick */

/* ---- act.c ---- */
void cfg_apply(void); /* push cfg into the hardware: display, LED, sound, log */
void act_display(u8 on);
void act_led(u8 on);
void act_sound(u8 on);
int reboot_busy(void); /* capturing, recording or not in photo/video: act_reboot refuses */
void act_reboot(void); /* returns only when refused, or still powered after 3 s */
s32 act_sound_gate(s32 id);    /* 1 = let stock play, 0 = return -1 like stock invalid id */
void act_long_select(void);    /* the C side of the K1 gesture */
void act_tick(void);           /* from v5_tick every pass: LCD, LED, chime, handback */
void act_host_released(void);  /* start the drive handback; act_tick writes LUN 0 once idle */

/* ---- webcam.c ---- */
extern u8 webcam_forced;         /* this boot is the webcam on every switch position */
extern u8 drive_forced;          /* this boot is the drive on every switch position */
s32 webcam_min(const char *s);   /* minutes 0..1440, -1 if not a number in range */
void webcam_cmd(s32 min);        /* `webcam [MIN]`: write the marker, reboot */
void drive_cmd(void);            /* `drive`: write the marker, reboot */
void once_boot(void);            /* from load_settings: act on the markers once per boot */
void webcam_tick(void);          /* from v5_tick: the reboot MIN minutes after the boot */

/* ---- xu.S (gap): the extension unit's mailbox, filled by a host SET_CUR ---- */
extern volatile u8 xu_box[];     /* XU_LEN bytes, xu.h */
extern volatile u8 xu_full;      /* 1 = xu_box holds a line the tick has not taken */

/* ---- stubs.S (gap) ---- */
CROSS u32 v5_irq_save(void);
CROSS void v5_irq_restore(u32 cpsr);
CROSS void v5_icache_inval(void); /* ARM; through a register, so the linker adds no veneer */

#endif /* V5_H */
