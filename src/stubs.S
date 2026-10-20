	.syntax unified
	.arm
	.code 32
	.section .text.stubs, "ax"

@ ARM-state entry points. Stock code reaches these through patched branches;
@ they call the Thumb C code with BLX through a literal, the same idiom the
@ firmware uses for its own libc thunks. They live in the RAM gap; the C
@ they call lives in the overlay (src/link.ld), except v5_set, the loader.
@
@ Rules every stub here follows:
@   - a site entered by B must leave the register file as it found it, apart
@     from the scratch registers its comment names, and must end with a B to
@     the address the original instruction would have fallen through to, or
@     to the other stock address its comment names. Returning through lr is
@     wrong at those sites: the enclosing stock function pops its own frame on
@     the way out.
@   - a site entered by BL returns with pop {r4, pc} against its own push,
@     with bx lr, or by tail-branching to the displaced callee.
@   - sp is 8-aligned before every C call.
@   - C preserves r4-r11, so a stock value live across the hook survives.
@   - until armed, a stub runs only the stock instruction it displaced (S1
@     also parks the power-on chime) and enters no C. SET is the exception:
@     it calls the loader, which is what makes arming possible.
@
@ Armed = st.booted and [0x123d34] == 1. st.booted is set only at the end of
@ the overlay's load_settings, which v5_set (src/loader.c) calls only after it
@ has read and checked the overlay on this boot; v5_set clears st.booted
@ before every load. SET runs after the card updater check on every fresh
@ mount, but not on the mount that starts an update (the update branch skips
@ the VERSION.TXT writer). [0x123d34] is stock .bss, so crt0 zeroes it on
@ every start; the check sets it to 2 when it starts the updater, and 0x3e580
@ sets it to 1 after the first mount call that did not, whatever that mount's
@ result. A card re-inserted during the update window reaches SET with 2;
@ v5_set returns at once then. So no overlay C runs before the updater,
@ during a flash, or when the overlay failed to load, and the updater's LED
@ blinks pass gpio_set untouched.

	.equ	ST_SOUND, 5
	.equ	ST_BOOTED, 6
	.equ	UPD_CHECKED, 0x123d34

	.macro	DCALL fn
	ldr	ip, =\fn
	blx	ip
	.endm

@ Z set when disarmed. Clobbers reg and the flags.
	.macro	ARMED reg
	ldr	\reg, =UPD_CHECKED
	ldrb	\reg, [\reg]
	cmp	\reg, #1
	ldreq	\reg, =st
	ldrbeq	\reg, [\reg, #ST_BOOTED]
	movne	\reg, #0
	cmp	\reg, #0
	.endm

	.global	v5_irq_save
	.type	v5_irq_save, %function
v5_irq_save:
	mrs	r0, cpsr
	orr	r1, r0, #0xc0
	msr	cpsr_c, r1
	bx	lr

	.global	v5_irq_restore
	.type	v5_irq_restore, %function
v5_irq_restore:
	mrs	r1, cpsr
	bic	r1, r1, #0xc0
	and	r0, r0, #0xc0
	orr	r1, r1, r0
	msr	cpsr_c, r1
	bx	lr

@ Invalidate the whole I-cache (ARMv5 cp15 c7,c5,0). ARM code: Thumb-1 has no
@ mcr. The firmware has D-cache range routines (0x40124, 0x40158) but no
@ I-cache invalidate; the loader calls this after cleaning the D-cache.
	.global	v5_icache_inval
	.type	v5_icache_inval, %function
v5_icache_inval:
	mov	r0, #0
	mcr	p15, 0, r0, c7, c5, 0
	bx	lr

@ LOG 0x537b4, in printf after vsnprintf. Entered by B, r4 = PRINT_BUF.
@ 0x5377c pops r4, r5 and lr, so all three are dead here; sp is not.
@ Disarmed: the displaced ldrb, then stock's UART output at 0x537b8, which
@ does not read ip. Armed: the line goes to logcap, then on to printf's
@ epilogue at 0x5377c, skipping the UART output.
	.global	logcap_stub
	.type	logcap_stub, %function
logcap_stub:
	ARMED	ip
	ldrbeq	r0, [r4]
	beq	0x537b8
	push	{r0-r3, r12, lr}
	mov	r0, r4
	DCALL	logcap
	pop	{r0-r3, r12, lr}
	b	0x5377c

@ SET 0x37fa0, epilogue of the VERSION.TXT writer on the mount path.
@ Displaces add sp, sp, #0x58; 0x37fa4 pops r4, so r4 must come back intact.
	.global	settings_stub
	.type	settings_stub, %function
settings_stub:
	add	sp, sp, #0x58
	push	{r4, lr}
	DCALL	v5_set
	pop	{r4, lr}
	b	0x37fa4

@ D1 0x22360, the 0x6001 tick in the peripheral task. Entered by BL, so the
@ stock seg_refresh call is ours to make. Disarmed: seg_refresh only.
	.global	tick_stub
	.type	tick_stub, %function
tick_stub:
	ARMED	ip
	beq	fw_seg_refresh
	push	{r4, lr}
	mov	r4, sp
	bic	sp, sp, #7
	bl	fw_seg_refresh
	DCALL	v5_tick
	mov	sp, r4
	pop	{r4, pc}

@ G1 0x1f5b0, VBUS state 0 with an insert edge. Entered by B. The stub lands
@ on 0x1f68c, where both stock post paths (0x1f600, 0x1f688) end, so
@ v5_usb_plugin posts the USB task message itself. Disarmed: the displaced
@ mov (r0 is dead until it) and on into stock's own insert path at 0x1f5b4,
@ which posts the base image's webcam init (0x8003).
	.global	plug_stub
	.type	plug_stub, %function
plug_stub:
	ARMED	r0
	mov	r0, #0x5f
	beq	0x1f5b4
	push	{r4, lr}
	mov	r4, sp
	bic	sp, sp, #7
	DCALL	v5_usb_plugin
	mov	sp, r4
	pop	{r4, lr}
	b	0x1f68c

@ G2 0x1f530, VBUS remove edge in state 1. Entered by BL. v5_usb_unplug calls
@ fw_usb_hw_off itself when it did not post an uninit, so the stub is plain.
@ Disarmed: the displaced bl 0xbdf04.
	.global	unplug_stub
	.type	unplug_stub, %function
unplug_stub:
	ARMED	ip
	beq	0xbdf04
	push	{r4, lr}
	mov	r4, sp
	bic	sp, sp, #7
	DCALL	v5_usb_unplug
	mov	sp, r4
	pop	{r4, pc}

@ G3a 0x22260, after cmp r3, #1 on the USB poll enable [0x131fc0]. Entered by
@ B in place of beq 0x2247c. Stock polls VBUS once a photo or video mount has
@ set the flag; armed, every tick polls. r3 is reloaded at 0x22264 and the
@ flags are set again at 0x2226c; 0x1f3d4 does not read r3.
	.global	usb_poll_gate_stub
	.type	usb_poll_gate_stub, %function
usb_poll_gate_stub:
	beq	0x2247c
	ARMED	r3
	bne	0x2247c
	b	0x22264

@ G3b 0x2247c, reached every tick once armed (G3a). The stock VBUS poll runs
@ first, then v5_usb_tick acts on the new state. Disarmed: the poll only.
	.global	usb_tick_stub
	.type	usb_tick_stub, %function
usb_tick_stub:
	ARMED	ip
	beq	fw_usb_vbus_tick
	push	{r4, lr}
	mov	r4, sp
	bic	sp, sp, #7
	bl	fw_usb_vbus_tick
	DCALL	v5_usb_tick
	mov	sp, r4
	pop	{r4, pc}

@ K1 0x1dda4, long press on Select. Entered by BHI, ends on the stock epilogue
@ at 0x1ddb8, which pops r4, r5 and lr from the caller's frame. Disarmed:
@ straight there, as the displaced bhi.
	.global	long_select_stub
	.type	long_select_stub, %function
long_select_stub:
	ARMED	ip
	beq	0x1ddb8
	push	{r4, lr}
	DCALL	act_long_select
	pop	{r4, lr}
	b	0x1ddb8

@ L1 0x433f4, gpio_set entry: the only writer of a GPIO data register, so this
@ is where LED off has to live. Runs in whatever task or handler called
@ gpio_set, so: no stack, no C, no locks. r2, r3 and ip are scratch there.
@ Disarmed: the displaced lsl only, nothing recorded. led is overlay data,
@ touched only armed.
	.global	v5_gpio_set
	.type	v5_gpio_set, %function
v5_gpio_set:
	ARMED	ip
	beq	1f
	cmp	r0, #0x2b
	cmpne	r0, #0x5f
	bne	1f
	ldr	r2, =led
	and	r3, r1, #1
	cmp	r0, #0x2b
	strbeq	r3, [r2, #1]
	strbne	r3, [r2, #2]
	ldrb	ip, [r2]
	cmp	ip, #0
	beq	1f
	cmp	r0, #0x2b
	ldrbeq	r1, [r2, #3]
	ldrbne	r1, [r2, #4]
1:	lsl	r3, r0, #0xc
	b	0x433f8

@ L2 0x1f5b4 and L3 0x1f65c, the two USB busy read-backs of GPIO 95. With the
@ LED forced off the pin may not show what stock asked for, so hand back the
@ level the LED gate last let through. Disarmed: the displaced bl 0x433b8.
	.global	v5_get95
	.type	v5_get95, %function
v5_get95:
	ARMED	ip
	beq	0x433b8
	ldr	r2, =led
	ldrb	r3, [r2]
	cmp	r3, #0
	ldrbne	r0, [r2, #2]
	bxne	lr
	b	0x433b8

@ S1 0x22a80, play_effect entry. Entered by B with r0 = effect id. Blocked
@ ids return -1, the stock invalid-handle value, before the amp is opened.
@ Disarmed (before the first mount, without a card, after a failed overlay
@ load, and for the whole canary run): ids 2 and 3 are parked in
@ st.sound_pending and return -1, every other id plays as stock. In V1.1.6
@ only the startup chime (0x35124) plays id 2, and nothing plays id 3; the
@ over-temperature warning (0x7017) plays id 0 through 0x27a30 and passes.
@ Once armed, chime() replays a parked id once if sound is on and the boot is
@ under 15 s old. Without a card or a good overlay nothing arms and the
@ parked id is never played.
	.global	v5_sound_gate
	.type	v5_sound_gate, %function
v5_sound_gate:
	ARMED	ip
	bne	2f
	cmp	r0, #2
	cmpne	r0, #3
	bne	1f
	ldr	ip, =st
	strb	r0, [ip, #ST_SOUND]
	mvn	r0, #0
	bx	lr
2:	push	{r4, lr}
	mov	r4, r0
	DCALL	act_sound_gate
	cmp	r0, #0
	mov	r0, r4
	pop	{r4, lr}
	bne	1f
	mvn	r0, #0
	bx	lr
1:	push	{r4, r5, r6, r7, lr}
	b	0x22a84

@ G6, the MSC eject callback word at 0xe0978 (SD LUN ops +0x30), reached with
@ blx r2 at 0x4e914 in the USB task, r0 = LUN. A tail call either way, so lr
@ still returns to 0x4e918. Disarmed: the stock callback 0xbe2d4. Armed: the
@ overlay's v5_eject_cb, which runs the stock callback itself.
	.global	eject_stub
	.type	eject_stub, %function
eject_stub:
	ARMED	ip
	beq	fw_stock_eject_cb
	ldr	ip, =v5_eject_cb
	bx	ip

	.ltorg
	.end
