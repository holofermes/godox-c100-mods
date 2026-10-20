#include "v5.h"
#include "fw.h"

/* USB personality per switch position (docs/usb.md).
 * Plug-in, unplug and tick run in the peripheral task, inside or right after
 * the stock VBUS poll, and usb_apply from v5_tick in the same task;
 * v5_eject_cb runs in the USB task. */

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
	w = cfg.usb[pos];
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
	 * SET_CONFIG (0x4ce80), which a webcam session would run: VBUS state 2,
	 * sensor powered down, power-off at unplug. 0x4ce80 skips a NULL. */
	else
		W_MSDC_CB = 0;
	if (!usb_post(msg)) {
		v5_log("usb %x fail", msg);
		return;
	}
	st.usb_active = want;
	v5_log("usb %u %s", B_MODE, name(want));
}

/* G1: VBUS state 0, insert edge. The stub goes on to 0x1f61c (state 1). */
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

/* G2: state 1, remove edge, in place of the stock bl 0xbde60. The webcam
 * uninit turns the PHY off itself (0x3ed80). A drive gets stock's PHY off
 * only: its uninit 0x8002 would run the SD driver uninit under the camera's
 * FS. */
void v5_usb_unplug(void)
{
	u8 was = st.usb_active;

	st.usb_active = USB_NONE;
	st.usb_pending = 0;
	v5_log("usb off %s", name(was));
	if (was == USB_WEBCAM && usb_post(USB_UVC_UNINIT))
		return;
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
	/* restart the charger count as 0x1f61c does after a stock post, so a
	 * drive post with no host still gets 0x6005 arg 1 (keys back) */
	W_USB_CHG_CNT = 0;
	usb_start();
}

/* G6: USB task, called from 0x4e740 on START STOP UNIT with LoEj after LUN 0
 * status was set to 0xffaa. Flag only; card work belongs to the tick. */
u32 v5_eject_cb(u32 lun)
{
	/* the stubs.S arm test */
	u8 armed = st.booted && B_UPD_CHECKED == 1;
	u32 r;

	/* fs_ready() is 0 from before the SD uninit until the tick remounts */
	if (armed)
		st.ejecting = 1;
	r = fw_stock_eject_cb(lun);
	if (armed) {
		st.host_released = 1;
		__asm__ volatile("" ::: "memory");
		st.ejecting = 0;
	}
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
