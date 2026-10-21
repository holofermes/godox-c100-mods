/* Two regions (docs/hooks.md):
 * gap: the unused RAM between the end of stock .data (0xe6968) and the start of
 *      stock .bss (0xe8000). The boot block copies it from the image; it holds
 *      what runs before the gate opens: stubs.S, the loader and its state, v6.S,
 *      xu.S.
 * ovl: the top 64 KB of RAM, which the patched heap-size literal keeps out of
 *      the FreeRTOS heap. The loader reads the overlay into it from NOR on the
 *      first card mount of a boot, or a later one if that load failed: a 16 B
 *      header at 0xff0000, the body from 0xff0010, then the overlay .bss, which
 *      the loader zeroes. */
MEMORY
{
	gap (rwx) : ORIGIN = 0xe6968, LENGTH = 0xe8000 - 0xe6968
	ovl (rwx) : ORIGIN = 0xff0010, LENGTH = 0x10000 - 0x10
}

SECTIONS
{
	/* stubs, loader and st sit at the same addresses in the full build and
	 * the canary; v6.S, then xu.S (full build only) come last */
	.gap : {
		__gap_start = .;
		*(.text.stubs)
		*loader.c.o(.text* .rodata* .data*)
		. = ALIGN(4);
		*loader.c.o(.bss* COMMON)
		. = ALIGN(4);
		*(.text.v6)
		*(.text.xu)
		__gap_end = .;
	} > gap

	__ovl_base = ORIGIN(ovl) - 0x10;
	.ovl : {
		__ovl_start = .;
		*(.text*)
		*(.rodata*)
		*(.data*)
		. = ALIGN(4);
		__ovl_load_end = .;
	} > ovl
	.ovl_bss (NOLOAD) : {
		__ovl_bss_start = .;
		*(.bss*)
		*(COMMON)
		. = ALIGN(4);
		__ovl_end = .;
	} > ovl
	/DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}

INCLUDE fw.ld
