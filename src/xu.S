#include "xu.h"

	.syntax unified
	.arm
	.code 32
	.section .text.xu, "ax"

@ The extension unit's EP0 requests (docs/webcam.md). The three hooks sit in
@ the handlers usb_uvc_init installs (0x52a90), which the USB device task runs
@ for each host request in webcam mode, so none of this runs at boot, during
@ the card updater, or without a host. Linked after v6.S in the gap: it works
@ without the overlay, and every earlier gap symbol keeps its address. It
@ calls no C and does nothing but the mailbox; the overlay's tick takes the
@ line and runs it (tick.c).
@
@ Requests for unit XU_ID on interface 0 (wIndex 0x0600), selector XU_SEL:
@   GET_INFO, 1 B   XU_INFO (GET and SET)
@   GET_LEN, 2 B    XU_LEN
@   GET_CUR         the line still waiting in the mailbox, zeros when none
@   SET_CUR         a line into the mailbox
@ wLength must be the size. Anything else stalls with a request error code,
@ the way stock stalls (code to 0x12440a, then 0xbc99c): 6 another selector,
@ 7 another request or length. SET_CUR is also refused with 2 while the
@ features are off (no overlay running) and with 1 while the last line still
@ waits for the tick.

	.equ	UVC_ERR, 0x12440a	@ request error code; GET_CUR entity 0 selector 2 returns it (0x51098)
	.equ	UPD_CHECKED, 0x123d34	@ 1 = the first mount ran and started no update (stubs.S)
	.equ	ST_BOOTED, 6		@ st.booted, v5.h
	.equ	XU_WINDEX, XU_ID << 8	@ unit XU_ID, interface 0
	.equ	XU_WVALUE, XU_SEL << 8
	.equ	E_NOT_READY, 1
	.equ	E_WRONG_STATE, 2
	.equ	E_CONTROL, 6
	.equ	E_REQUEST, 7

	.if	XU_LEN % 16
	.error	"xu_data copies XU_LEN in 16 B steps"
	.endif

@ EP0 state 0x134e34 (r4 or r5 below): +0x28 data pointer, +0x2c length,
@ +0x31 bRequest, +0x32 wValue, +0x34 wIndex, +0x36 wLength.

@ 0x50980, class request IN, entered by B from the setup handler 0x507ac,
@ whose frame (push {r4-r8, lr}) is live, r4 = 0x134e34. Not ours: the
@ displaced ldrh r2 (wIndex) and on at 0x50984, which sets the flags again.
@ Ours: answered through stock's send at 0x509d4 (clamp to wLength, FIFO
@ write with word loads, so the data is 4-aligned) or stalled through
@ 0x50930; both pop that frame.
	.global	xu_in
	.type	xu_in, %function
xu_in:
	ldrh	r2, [r4, #0x34]
	cmp	r2, #XU_WINDEX
	bne	0x50984
	ldrb	r0, [r4, #0x31]
	ldrh	r1, [r4, #0x32]
	ldrh	r3, [r4, #0x36]
	mov	r2, #E_CONTROL
	cmp	r1, #XU_WVALUE
	bne	xu_stall
	mov	r2, #E_REQUEST
	cmp	r0, #0x81		@ GET_CUR
	ldreq	r0, =xu_box
	moveq	r1, #XU_LEN
	beq	1f
	cmp	r0, #0x85		@ GET_LEN
	ldreq	r0, =xu_len
	moveq	r1, #2
	beq	1f
	cmp	r0, #0x86		@ GET_INFO
	bne	xu_stall
	ldr	r0, =xu_info
	mov	r1, #1
1:	cmp	r3, r1
	bne	xu_stall
	str	r0, [r4, #0x28]
	str	r1, [r4, #0x2c]
	ldr	r1, =UVC_ERR
	mov	r0, #0
	strb	r0, [r1]
	b	0x509d4

@ 0x507f0, class request OUT, entered by B from 0x507ac with its frame live,
@ r4 = 0x134e34. Not ours: the displaced ldrb r3 (interface) and on at
@ 0x507f4, which sets the flags again. Ours and acceptable: 0x50808, stock's
@ own start of a SET_CUR data stage (+0x2c = wLength, then 0xbcb18 arms
@ the OUT FIFO); the data lands in xu_data. Else a stall: a bad SET_CUR
@ never gets a data stage.
	.global	xu_out
	.type	xu_out, %function
xu_out:
	ldrh	r3, [r4, #0x34]
	cmp	r3, #XU_WINDEX
	ldrbne	r3, [r4, #0x34]
	bne	0x507f4
	ldrb	r0, [r4, #0x31]
	ldrh	r1, [r4, #0x32]
	ldrh	r3, [r4, #0x36]
	mov	r2, #E_CONTROL
	cmp	r1, #XU_WVALUE
	bne	xu_stall
	mov	r2, #E_REQUEST
	cmp	r0, #1			@ SET_CUR
	cmpeq	r3, #XU_LEN
	bne	xu_stall
	@ the features are on: the stubs' ARMED test (stubs.S)
	mov	r2, #E_WRONG_STATE
	ldr	r0, =UPD_CHECKED
	ldrb	r0, [r0]
	cmp	r0, #1
	bne	xu_stall
	ldr	r0, =st
	ldrb	r0, [r0, #ST_BOOTED]
	cmp	r0, #0
	beq	xu_stall
	mov	r2, #E_NOT_READY
	ldr	r0, =xu_full
	ldrb	r0, [r0]
	cmp	r0, #0
	bne	xu_stall
	b	0x50808

@ r2 = the error code. 0x50930 pops 0x507ac's frame and stalls EP0 (0xbc99c).
xu_stall:
	ldr	r1, =UVC_ERR
	strb	r2, [r1]
	b	0x50930

@ 0x52570, the SET_CUR data stage in the OUT-done handler 0x524b8, after it
@ zeroed 64 B at 0x124428 (r7) and read the packet there, r6 = interface; its
@ frame (push {r4-r8, lr}) is live, r5 = 0x134e34. Only a SET_CUR xu_out
@ accepted gets here with the unit's wIndex. Not ours: the displaced
@ cmp r6, #1 and on at 0x52574. Ours: the line into the mailbox, the flag
@ last, then stock's status stage at 0x52530 (+0x2c = 0, zero-length IN).
@ A short packet leaves zeros at the end, which pad the line.
	.global	xu_data
	.type	xu_data, %function
xu_data:
	ldrh	r3, [r5, #0x34]
	cmp	r3, #XU_WINDEX
	beq	1f
	cmp	r6, #1
	b	0x52574
1:	ldr	r0, =xu_box
	.rept	XU_LEN / 16
	ldm	r7!, {r1, r2, r3, ip}
	stm	r0!, {r1, r2, r3, ip}
	.endr
	mov	r1, #1
	strb	r1, [r0]		@ xu_full, right after xu_box
	ldr	r1, =UVC_ERR
	mov	r0, #0
	strb	r0, [r1]
	b	0x52530

	.ltorg

@ The mailbox. Zero in the image, so zero after every boot copy. Written
@ here (USB task) only while xu_full is 0; the tick (peripheral task) copies
@ the line, zeroes the box and clears xu_full with IRQs off.
	.balign	4
	.global	xu_box
	.type	xu_box, %object
xu_box:
	.space	XU_LEN
	.size	xu_box, XU_LEN
	.global	xu_full
	.type	xu_full, %object
xu_full:
	.byte	0
	.size	xu_full, 1

@ GET_LEN and GET_INFO answers; 4-aligned for 0xbc9cc's word loads
	.balign	4
xu_len:
	.2byte	XU_LEN
	.balign	4
xu_info:
	.byte	XU_INFO
	.balign	4

	.end
