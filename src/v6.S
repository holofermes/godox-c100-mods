	.syntax unified
	.arm
	.code 32
	.section .text.v6, "ax"

@ Webcam gates (docs/webcam.md), not part of the canary build. Neither runs
@ at boot: the aspect gate runs on a left/right press, the record gate on a
@ record start.
@
@ Blocked = the host streams: EP5 alt at 0x123d04, stored by 0x3e410, 3 while
@ streaming, 0 when closed.

	.equ	VS_ALT, 0x123d04

@ Z clear when blocked. Clobbers ip and the flags.
	.macro	BLOCKED
	ldr	ip, =VS_ALT
	ldr	ip, [ip]
	cmp	ip, #0
	.endm

@ Aspect step 0x21318, from the BL at its three callers (photo 0x2ea90, video
@ 0x36f4c and 0x36f7c). Blocked, the index at 0x12178c stays put, the caller
@ finds it equal to the stored setting and skips the mode re-entry that
@ stops the webcam encoder. r0 is the step; the callers ignore it after.
	.global	v6_aspect_gate
	.type	v6_aspect_gate, %function
v6_aspect_gate:
	BLOCKED
	bxne	lr
	b	0x21318

@ Record start, from the BEQ at 0x356e0 in the record start/stop function
@ 0x356a8. Blocked, 0x356e4 returns 0 with no state change; else on to the
@ start at 0x35858, which needs r3 (the record flags) and r5 as they are.
	.global	v6_rec_gate
	.type	v6_rec_gate, %function
v6_rec_gate:
	BLOCKED
	bne	0x356e4
	b	0x35858

	.ltorg
	.end
