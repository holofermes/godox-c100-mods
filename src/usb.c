#include "v5.h"
#include "fw.h"

/* USB personality per switch position (docs/usb.md).
 * Plug-in, unplug and tick run in the peripheral task, inside or right after
 * the stock VBUS poll, and usb_apply from v5_tick in the same task;
 * v5_eject_cb runs in the USB task. */

#define STOP_WAIT 500 /* ms for uvc_task and the USB task to stop a stream (0x8008) */

static const char *const pname[] = { "none", "drive", "webcam" };

static u8 noted; /* last wanted personality usb_apply reported */

static const char *name(u8 p)
{
	return p < 3 ? pname[p] : "?";
}

/* Stock skips the post while GPIO 95 is high (capture or recording). With
 * the LED forced off the pin lies; led.want95 is what stock last wrote. */
static int bus_busy(void)
{
	if (led.off)
		return led.want95;
	return fw_gpio_read(PIN_95) != 0;
}

/* The first mount result is in: B_MODE and the card state are valid. Armed,
 * the VBUS poll runs every tick (G3a), so a plug-in can arrive before this. */
static int usb_ready(void)
{
	return B_USB_POLL_EN == 1 || B_MOUNT_FAIL == 1;
}

static u8 usb_want(void)
{
	u32 pos = B_MODE;
	u8 w;

	if (pos >= NPOS)
		pos = POS_PHOTO;
	/* a boot from `webcam` or `drive`: every position (webcam.c) */
	if (webcam_forced)
		return USB_WEBCAM;
	w = drive_forced ? USB_DRIVE : cfg.usb[pos];
	/* without a card usb_msdc_init exports LUN type 4, nothing useful */
	if (w == USB_DRIVE && (B_MOUNT_FAIL || fw_card_id() == 0xff))
		w = USB_WEBCAM;
	return w;
}

static int usb_post(u32 msg)
{
	if (!USBQ)
		return 0;
	return usbq_post(msg) == 1;
}

/* caller checked usb_ready() and !bus_busy() */
static void usb_start(void)
{
	u8 want = usb_want();
	u32 msg = want == USB_DRIVE ? USB_MSDC_INIT : USB_UVC_INIT;

	st.usb_pending = 0;
	st.usb_active = USB_NONE;
	noted = want;
	if (want == USB_NONE)
		return;
	/* the host reads the FAT from the sectors, not from the camera's cache */
	if (want == USB_DRIVE)
		fs_flush();
	/* A drive init earlier in this power cycle left its callback for
	 * SET_CONFIG (0x4d054), which a webcam session would run: VBUS state 2,
	 * sensor powered down, power-off at unplug. 0x4d054 skips a NULL. */
	else
		W_MSDC_CB = 0;
	if (!usb_post(msg)) {
		v5_log("usb %x fail", msg);
		return;
	}
	st.usb_active = want;
	v5_log("usb %u %s", B_MODE, name(want));
}

/* G1: VBUS state 0, insert edge. The stub goes on to 0x1f68c (state 1). */
void v5_usb_plugin(void)
{
	st.usb_active = USB_NONE;
	st.usb_pending = 0;
	if (!usb_ready() || bus_busy()) {
		/* usb_start re-reads the switch and cfg when it finally posts */
		st.usb_pending = usb_want();
		v5_log("usb wait %s", usb_ready() ? "busy" : "mount");
		return;
	}
	usb_start();
}

/* G2: state 1, remove edge, in place of the stock bl 0xbdf04. The webcam
 * uninit turns the PHY off itself (0x3ef50). A drive gets stock's PHY off
 * only: its uninit 0x8002 would run the SD driver uninit under the camera's
 * FS.
 *
 * A stream still running when the cable goes is stopped first, the way the
 * host's alt 0 stops it. A pulled cable gives only a bus suspend, which stock
 * does not act on (0x52428), and an EP5 transfer that never completes.
 * 0x3e5e0(0) is stock's alt-setting callback (registered by 0x528dc, called
 * at 0x52424 on SET_INTERFACE): it posts the stop to uvc_task, which hands
 * its frame back to the encoder and posts 0x8008, whose stop the USB task
 * runs before the uninit posted here. The uninit itself no longer closes the
 * encoder (tools/patch.py nops its 0x3ec00): that close freed the frame
 * buffers under a frame still on EP5, which turned the camera off, never got
 * a reply and left the session flagged open. The stream stopped this way
 * leaves the session open, as every host stop does, and the next stream
 * reuses it. */
void v5_usb_unplug(void)
{
	u8 was = st.usb_active;

	st.usb_active = USB_NONE;
	st.usb_pending = 0;
	v5_log("usb off %s", name(was));
	if (was == USB_WEBCAM) {
		if (W_VS_ALT) {
			fw_uvc_alt(0);
			fw_delay(STOP_WAIT);
		}
		if (usb_post(USB_UVC_UNINIT))
			return;
	}
	fw_usb_hw_off();
}

/* G3b: every 0x6000 tick, after fw_usb_vbus_tick() */
void v5_usb_tick(void)
{
	/* an eject that outlived its drive session has nothing to hand back */
	if (st.host_released && st.usb_active != USB_DRIVE)
		st.host_released = 0;
	if (!st.usb_pending || !usb_ready())
		return;
	if (B_USB_STATE != 1 || bus_busy())
		return;
	/* restart the charger count as 0x1f68c does after a stock post, so a
	 * drive post with no host still gets 0x6005 arg 1 (keys back) */
	W_USB_CHG_CNT = 0;
	usb_start();
}

/* G6: USB task, from blx r2 at 0x4e914 on START STOP UNIT with LoEj after LUN 0
 * status was set to 0xffaa, through eject_stub, which comes here only armed
 * and runs the stock callback alone otherwise. Flag only; card work belongs
 * to the tick. */
u32 v5_eject_cb(u32 lun)
{
	u32 r;

	/* fs_ready() is 0 from before the SD uninit until the tick remounts */
	st.ejecting = 1;
	r = fw_stock_eject_cb(lun);
	st.host_released = 1;
	__asm__ volatile("" ::: "memory");
	st.ejecting = 0;
	return r;
}

/* From the 0x6001 tick. A posted personality stays until the next plug-in:
 * a live swap is not safe. A deferred post picks up the current switch and
 * cfg by itself. Here: say once when they disagree. */
void usb_apply(void)
{
	u8 want;

	if (st.usb_active == USB_NONE || st.usb_pending)
		return;
	want = usb_want();
	if (want == noted)
		return;
	noted = want;
	if (want != st.usb_active)
		v5_log("usb %u %s at replug", B_MODE, name(want));
}
