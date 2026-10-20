	.syntax unified
	.arm
	.code 32
	.section .text.v6, "ax"

@ Webcam gates (docs/webcam.md), not part of the canary build. Neither runs
@ at boot: the aspect gate runs on a left/right press, the record gate on a
@ record start. They call no C, so they live in the RAM gap and work whether
@ or not the overlay loaded.
@
@ Blocked = the host streams: EP5 alt at 0x123d3c, stored by 0x3e5e0, 3 while
@ streaming, 0 when closed.

	.equ	VS_ALT, 0x123d3c

@ Z clear when blocked. Clobbers ip and the flags.
	.macro	BLOCKED
	ldr	ip, =VS_ALT
	ldr	ip, [ip]
	cmp	ip, #0
	.endm

@ Aspect step 0x213e0, from the BL at its three callers (photo 0x2ec48, video
@ 0x37104 and 0x37134). Blocked, the index at 0x1217bd stays put, the caller
@ finds it equal to the stored setting and skips the mode re-entry that
@ stops the webcam encoder. r0 is the step; the callers ignore it after.
	.global	v6_aspect_gate
	.type	v6_aspect_gate, %function
v6_aspect_gate:
	BLOCKED
	bxne	lr
	b	0x213e0

@ Record start, from the BEQ at 0x35898 in the record start/stop function
@ 0x35860. Blocked, 0x3589c returns 0 with no state change; else on to the
@ start at 0x35a10, which needs r3 (the record flags) and r5 as they are.
	.global	v6_rec_gate
	.type	v6_rec_gate, %function
v6_rec_gate:
	BLOCKED
	bne	0x3589c
	b	0x35a10

	.ltorg
	.end
