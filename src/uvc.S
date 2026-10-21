	.syntax unified
	.arm
	.code 32
	.section .text.uvc, "ax"

@ Webcam init after a teardown (docs/usb.md). 0xbdf04, the USB block off that
@ the webcam uninit 0x8005 and every unplug run, sets bits 0x8200 of the system
@ control word 0xd0000008 (0xbdf2c). The drive init clears both before its
@ controller reset (0xbdd40, 0xbdd50); the webcam init's 0xbdf3c clears only
@ 0x100 and 0x8000. With 0x200 left set (`rSYS_CTRL_NEW =0x29c` instead of
@ the boot's 0x9c) the host sees a full-speed device that answers nothing, so
@ a webcam never came back after an unplug. This stub, in place of the
@ `bl 0xbdf3c` at the start of usb_uvc_init, clears 0x8200 the drive init's
@ way, then runs 0xbdf3c.
@
@ usb_uvc_init runs in the USB task on 0x8003, posted at a webcam plug-in,
@ which comes only after the first card mount and its update check. Linked
@ after wdt.S: every earlier gap symbol keeps its address. Not in the canary.

	.equ	SYS_CTRL, 0xd0000008

@ 0x3eda4, entered by BL. 0xbdf3c uses r2 and r3; so does this.
	.global	uvc_init_sysctl
	.type	uvc_init_sysctl, %function
uvc_init_sysctl:
	ldr	r3, =SYS_CTRL
	ldr	r2, [r3]
	bic	r2, r2, #0x8200
	str	r2, [r3]
	b	0xbdf3c

	.ltorg
	.end
