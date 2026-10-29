	.syntax unified
	.arm
	.code 32
	.section .text.wdt, "ax"

@ The restart (src/act.c act_reboot, docs/usb.md) is stock's 0x1fbac letting
@ the SoC watchdog run out after it armed an RTC alarm to power the camera
@ back up. While the webcam is up, uvc_task kicks the watchdog on every pass
@ of its loop, at most 1 s apart, so the reset never came. This stub takes
@ that kick and skips it once 0x1fbac has started.
@
@ [0x12166e] is stock .bss, so 0 after every boot. 0x1fbac sets it to 1
@ (0x1fbd8) after it switched the watchdog to period 7 and right before it
@ arms the alarm, and never clears it: it spins in `b .` after. The only
@ other writer, 0x1fa58, sets and clears it around its own alarm and is
@ dormant (gated on [0x131fb8], which nothing writes). So the kicks stop only
@ once the alarm that powers the camera back up is being armed.
@
@ uvc_task exists only between the webcam init 0x8003 and its uninit 0x8005,
@ so this never runs at boot or during the card updater. Linked after xu.S:
@ every earlier gap symbol keeps its address. Not in the canary.

	.equ	REBOOTING, 0x12166e

@ 0x3e6d4, `bl 0x47014` in uvc_task 0x3e6a0, right after its queue wait;
@ entered by BL. 0x47014 writes the kick key and uses r2 and r3; nothing
@ after the call reads r0-r3 or ip before setting them.
	.global	wdt_uvc_kick
	.type	wdt_uvc_kick, %function
wdt_uvc_kick:
	ldr	ip, =REBOOTING
	ldrb	ip, [ip]
	cmp	ip, #0
	bxne	lr
	b	0x47014

	.ltorg
	.end
