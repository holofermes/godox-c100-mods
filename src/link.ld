/* The payload lives in the unused RAM gap between the end of stock .data
 * (0xe6828) and the start of stock .bss (0xe8000). */
MEMORY
{
	gap (rwx) : ORIGIN = 0xe6828, LENGTH = 0xe8000 - 0xe6828
}

SECTIONS
{
	__v5_start = ORIGIN(gap);
	.text : {
		*(.text.stubs)
		*(.text*)
		*(.rodata*)
	} > gap
	.data : { *(.data*) } > gap
	.bss : { *(.bss*) *(COMMON) } > gap
	__v5_end = .;
	/DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}

INCLUDE fw.ld
