	.syntax unified
	.arm
	.code 32
	.section .text.stubs, "ax"

@ ARM-state entry points. Stock code reaches these through patched branches;
@ they call the Thumb C code with BLX through a literal, the same idiom the
@ firmware uses for its own libc thunks.
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
@     it is what arms.
@
@ Armed = st.booted and [0x123cfc] == 1. st.booted is set at the end of
@ load_settings: SET runs after the card updater check on every mount and
@ never on a boot that flashes (the update branch skips the VERSION.TXT
@ writer). [0x123cfc] is stock .bss, so crt0 zeroes it on every start; the
@ check sets it to 2 when it starts the updater, and 0x3e3b0 sets it to 1
@ after a first mount that did not. st sits in the gap, which crt0 leaves
@ alone, so a restart that skipped the boot loader copy would keep st.booted.
@ So no payload C runs before the updater or during a flash, and the
@ updater's LED blinks pass gpio_set untouched.

	.equ	ST_SOUND, 5
	.equ	ST_BOOTED, 6
	.equ	UPD_CHECKED, 0x123cfc

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

@ LOG 0x535e0, in printf after vsnprintf. Entered by B, r4 = PRINT_BUF.
@ 0x535a8 pops r4, r5 and lr, so all three are dead here; sp is not.
@ Disarmed: the displaced ldrb, then stock's UART output at 0x535e4, which
@ does not read ip.
	.global	logcap_stub
	.type	logcap_stub, %function
logcap_stub:
	ARMED	ip
	ldrbeq	r0, [r4]
	beq	0x535e4
	push	{r0-r3, r12, lr}
	mov	r0, r4
	DCALL	logcap
	pop	{r0-r3, r12, lr}
	b	0x535a8

@ SET 0x37df4, epilogue of the VERSION.TXT writer on the mount path.
@ Displaces add sp, sp, #0x58; 0x37df8 pops r4, so r4 must come back intact.
	.global	settings_stub
	.type	settings_stub, %function
settings_stub:
	add	sp, sp, #0x58
	push	{r4, lr}
	DCALL	load_settings
	pop	{r4, lr}
	b	0x37df8

@ D1 0x22238, the 0x6001 tick in the peripheral task. Entered by BL, so the
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

@ G1 0x1f540, VBUS state 0 with an insert edge. Entered by B. The stub lands
@ on 0x1f61c, past the stock post site at 0x1f608, so v5_usb_plugin posts the
@ USB task message itself. Disarmed: the displaced mov (r0 is dead until it)
@ and on into stock's own insert path at 0x1f544, which posts the base
@ image's webcam init (0x8003).
	.global	plug_stub
	.type	plug_stub, %function
plug_stub:
	ARMED	r0
	mov	r0, #0x5f
	beq	0x1f544
	push	{r4, lr}
	mov	r4, sp
	bic	sp, sp, #7
	DCALL	v5_usb_plugin
	mov	sp, r4
	pop	{r4, lr}
	b	0x1f61c

@ G2 0x1f4c0, VBUS remove edge in state 1. Entered by BL. v5_usb_unplug calls
@ fw_usb_hw_off itself when it did not post an uninit, so the stub is plain.
@ Disarmed: the displaced bl 0xbde60.
	.global	unplug_stub
	.type	unplug_stub, %function
unplug_stub:
	ARMED	ip
	beq	0xbde60
	push	{r4, lr}
	mov	r4, sp
	bic	sp, sp, #7
	DCALL	v5_usb_unplug
	mov	sp, r4
	pop	{r4, pc}

@ G3a 0x22138, after cmp r3, #1 on the USB poll enable [0x131f88]. Entered by
@ B in place of beq 0x22354. Stock polls VBUS once a photo or video mount has
@ set the flag; armed, every tick polls. r3 is reloaded at 0x2213c and the
@ flags are set again at 0x22144; 0x1f364 does not read r3.
	.global	usb_poll_gate_stub
	.type	usb_poll_gate_stub, %function
usb_poll_gate_stub:
	beq	0x22354
	ARMED	r3
	bne	0x22354
	b	0x2213c

@ G3b 0x22354, reached every tick once armed (G3a). The stock VBUS poll runs
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

@ L1 0x43224, gpio_set entry: the only writer of a GPIO data register, so this
@ is where LED off has to live. Runs in whatever task or handler called
@ gpio_set, so: no stack, no C, no locks. r2, r3 and ip are scratch there.
@ Disarmed: the displaced lsl only, nothing recorded.
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
	b	0x43228

@ L2 0x1f544 and L3 0x1f5ec, the two USB busy read-backs of GPIO 95. With the
@ LED forced off the pin may not show what stock asked for, so hand back the
@ level the LED gate last let through. Disarmed: the displaced bl 0x431e8.
	.global	v5_get95
	.type	v5_get95, %function
v5_get95:
	ARMED	ip
	beq	0x431e8
	ldr	r2, =led
	ldrb	r3, [r2]
	cmp	r3, #0
	ldrbne	r0, [r2, #2]
	bxne	lr
	b	0x431e8

@ S1 0x22958, play_effect entry. Entered by B with r0 = effect id. Blocked
@ ids return -1, the stock invalid-handle value, before the amp is opened.
@ Disarmed, only the power-on chime can play (state 0x100 drops key messages
@ and the ADC scan is off until photo/video entry): ids 2 and 3 are parked in
@ st.sound_pending for chime() to replay once C100.TXT is known, the rest play
@ as stock. Without a card nothing arms: the chime stays parked and every
@ other sound plays as stock.
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
	b	0x2295c

	.end
