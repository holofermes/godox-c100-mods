#ifndef FW_H
#define FW_H

#include <stdarg.h> /* va_list, for fw_vsnprintf */

/* Stock V1.1.3 functions and variables, app RAM addresses.
 * Calls go through pointers so Thumb code reaches ARM code with BLX.
 * file offset = app RAM + 0x10200.
 * Each address here was read in the image; check the original bytes in
 * obj/base.bin before adding one. Not all of them are called. */

#define FW(ret, addr, ...) ((ret (*)(__VA_ARGS__))(addr))
#define FW8(addr) (*(volatile u8 *)(addr))
#define FW16(addr) (*(volatile u16 *)(addr))
#define FW32(addr) (*(volatile u32 *)(addr))

/* ---- heap and print ---- */
#define fw_malloc FW(void *, 0x53578, u32)
#define fw_free FW(void, 0x53580, void *)
/* stock printf: the formatted line lands at PRINT_BUF, then goes to a dead UART,
 * or to the log ring once the LOG hook at 0x535e0 is armed. */
#define fw_printf FW(s32, 0x53584, const char *, ...)
#define PRINT_BUF 0x124438 /* 512 B, NUL-terminated, no lock: task context only */

/* ---- file API. Each wrapper takes the fs mutex itself; it is a plain mutex, not
 * recursive, so never call one while holding fw_fs_take ---- */
#define fw_open FW(s32, 0x9e434, const char *, u32)
#define fw_read FW(s32, 0x9e4a8, s32, void *, u32)
#define fw_write FW(s32, 0x9e7f4, s32, const void *, u32)
#define fw_lseek FW(s32, 0x9e2e0, s32, s32, u32) /* whence 0 SET 1 CUR 2 END */
#define fw_close FW(s32, 0x9daa8, s32)
#define fw_unlink FW(s32, 0x9e798, const char *)
#define fw_stat FW(s32, 0x9e6c0, const char *, u32 *) /* 0 = exists; see fs_size */
#define fw_rename FW(s32, 0x9e518, const char *, const char *) /* 0 / -1, stock 0x39e1c */
#define fw_chdir FW(s32, 0x9da40, const char *)
#define fw_flush FW(void, 0x9aa98, u32)               /* 6 = drive G: */
#define fw_unmount FW(s32, 0x9ddbc, u32)              /* fails 0xffff if a file is open */
#define fw_mount FW(s32, 0x9dd78, u32)
#define fw_sd_init FW(s32, 0xadac8, u32)
#define fw_fs_take FW(void, 0xa0cdc)
#define fw_fs_give FW(void, 0xa0cf8)

/* open flags, as collapsed by the wrapper */
#define O_READ_ONLY 0x000 /* exists test: fd >= 0 iff the file is there */
#define O_READ_WRITE 0x001 /* existing, no truncate, pos 0; pair with lseek END */
#define O_CREATE_TRUNC 0x601 /* create/truncate/write */
#define DRIVE_G 6

/* paths are 8.3 with a drive prefix and backslashes */
#define P_LOG "G:\\LOG.TXT"
#define P_CFG "G:\\C100.TXT"
#define P_CMD "G:\\CMD.TXT"
#define P_FW ((const char *)0xd1f40) /* stock "G:\\GP_CARDVR_UPGRADE.BIN" */

/* ---- OS ---- */
/* newlib lives in the image; ARM reaches it through these thunks */
#define fw_strlen FW(u32, 0xce034, const char *)
#define fw_strcmp FW(s32, 0xce138, const char *, const char *)
#define fw_memcpy FW(void *, 0xcdf80, void *, const void *, u32)
#define fw_memset FW(void *, 0xce0d0, void *, int, u32)
#define fw_vsnprintf FW(s32, 0xce11c, char *, u32, const char *, va_list)
#define fw_ticks FW(u32, 0x5dd10, void) /* 1 ms */
#define fw_delay FW(void, 0x5e03c, u32) /* ticks, blocks */
#define fw_cs_enter FW(void, 0x5c7b0)
#define fw_cs_exit FW(void, 0x5c7d0)
#define fw_qsend FW(s32, 0x5cfe0, void *, const void *, u32, u32) /* xQueueSend */

/* ---- gpio, display, led, sound ---- */
#define fw_gpio_set FW(void, 0x43224, u32, u32)
#define fw_gpio_read FW(u32, 0x431e8, u32)
#define fw_ht_cmd FW(void, 0x49e4c, u32)    /* 0x02 LCD OFF, 0x03 LCD ON */
#define fw_ht_write FW(void, 0x49c64, u32, u32)
#define fw_led_level FW(void, 0x1fc3c, u32) /* 1 -> GPIO 95 low, 0/2/3 -> high */
#define fw_play_effect FW(s32, 0x22958, s32)
#define fw_seg_refresh FW(void, 0x21250)
#define fw_rtc_read FW(s32, 0x4519c, s32 *) /* out[0..3] = sec min hour days, <0 = failed */
#define fw_reboot FW(void, 0x1fb3c)          /* RTC-alarm power-off, never returns */
#define fw_power_off FW(void, 0x26140, u32)
#define fw_stock_eject_cb FW(u32, 0xbe230, u32) /* replaced by v5_eject_cb at 0xe0848 */
#define fw_usb_vbus_tick FW(void, 0x1f364)      /* stock VBUS edge poll, peripheral task */
#define fw_usb_hw_off FW(void, 0xbde60)         /* stock: tear the device down in place */
#define fw_apq_flush FW(void, 0x644, void *)    /* drop pending app messages */

#define PIN_43 43 /* green status LED, lit at 1 (seen on the camera) */
#define PIN_95 95 /* SDK TFT backlight reused as LED, capture/record busy */
#define PIN_44 44 /* speaker amp */

#define HT_LCD_OFF 0x02
#define HT_LCD_ON 0x03

/* ---- queues and app messages ---- */
#define APQ FW32(0x1329dc)  /* msgQ object pointer */
#define APQ_ITEM 0x28       /* ApQ data item size */
#define USBQ FW32(0x123d34) /* USB task queue handle */
#define fw_msg_post FW(s32, 0x42c, void *, u32, const void *, u32, s32)

/* post msg to the app queue; a1, a2 travel as the data item's first two words */
static inline s32 apq_post(u32 msg, u32 a1, u32 a2, s32 urgent)
{
	u32 item[APQ_ITEM / 4];
	int i;
	for (i = 0; i < (int)(APQ_ITEM / 4); i++)
		item[i] = 0;
	item[0] = a1;
	item[1] = a2;
	return fw_msg_post((void *)APQ, msg, item, APQ_ITEM, urgent);
}

/* post a word to the USB task queue (stock idiom at 0x1f578..0x1f590) */
static inline s32 usbq_post(u32 msg) { return fw_qsend((void *)USBQ, &msg, 0, 0); }

#define MSG_POWER_OFF 0x7004
#define MSG_REBOOT 0x7007
#define MSG_CONNECT_PC 0x700e
#define MSG_DISCONNECT_PC 0x700f
#define MSG_MODE_PHOTO 0x7040
#define MSG_MODE_VIDEO 0x703f
#define MSG_MODE_METER 0x703e
#define MSG_CLICK 0x7018

#define USB_MSDC_INIT 0x8001
#define USB_MSDC_UNINIT 0x8002
#define USB_UVC_INIT 0x8003
#define USB_UVC_UNINIT 0x8005

/* ---- stock state bytes ---- */
#define B_MODE FW8(0x1216d6)       /* switch position 0 photo 1 video 2 metering */
#define W_APP_STATE FW32(0x132854) /* 0x100 startup 0x101 video 0x102 photo */
#define B_CARD_OK FW8(0x121965)    /* 1 = card mounted and usable */
#define B_UPD_CHECKED FW8(0x123cfc) /* 0 at boot, 2 updater started, 1 first mount found none */
#define B_MOUNT_FAIL FW8(0x1216ed) /* set by the photo/video mount-fail handlers */
#define B_USB_POLL_EN FW8(0x131f88) /* 1 once a photo/video mount OK ran, never cleared */
#define B_USB_STATE FW8(0x12162d)  /* VBUS state machine state (0x1f364) */
#define W_USB_CHG_CNT FW32(0x121660) /* state 1 polls since the plug-in post */
#define B_CAPTURE_BUSY FW8(0x1218e6)
#define B_REC_FLAGS FW8(0x132a83) /* bit1 recording, bit3 record transition */
#define H_LUN0 FW16(0x124278)     /* MSC LUN 0 status; writes must be 16-bit */
#define W_MSDC_CB FW32(0x124254)  /* SET_CONFIG callback: only writer 0x4d488 from usb_msdc_init
				   * 0x3e898, only reader 0x4ce80, which skips NULL */
#define LUN_EJECTED 0xffaa        /* host ejected: the camera may use the card */
#define B_F_HELD FW8(0x12158f)    /* Select held-repeat latch, 0 = first call */
#define B_KEYSOUND FW8(0x132128 + 0x19)
#define W_PRINTF_FLAGS FW32(0xe3ac8) /* bit0 = format to PRINT_BUF */
#define CUR_TCB FW32(0x126b34)       /* FreeRTOS pxCurrentTCB */
#define PERIPH_TCB FW32(0x19296c)    /* peripheral task handle, InitTask 0x5efbc */
#define fw_card_id FW(u32, 0x26118, void) /* 0xff = no card, as usb_msdc_init tests it */

#endif /* FW_H */
