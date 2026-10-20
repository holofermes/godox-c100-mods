#!/bin/bash
# Build the patched firmware from the stock V1.1.6 GP_CARDVR_UPGRADE.BIN.
# usage: ./build.sh STOCK.BIN            -> build/GP_CARDVR_UPGRADE.BIN
#        CANARY=1 ./build.sh STOCK.BIN   -> build-canary/GP_CARDVR_UPGRADE.BIN (docs/hooks.md)
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
[ $# -eq 1 ] || { echo "usage: $0 STOCK.BIN" >&2; exit 1; }
stock=$(realpath "$1")

canary=${CANARY:-0}
if [ "$canary" = 1 ]; then
	obj="$here/obj-canary"
	outdir="$here/build-canary"
	srcs=("$here/src/loader.c" "$here/src/state.c" "$here/canary/canary.c" "$here/src/stubs.S")
else
	obj="$here/obj"
	outdir="$here/build"
	srcs=("$here"/src/*.c "$here"/src/*.S)
fi
mkdir -p "$obj" "$outdir"
# a failed build must not leave an older image looking current
rm -fv "$outdir/GP_CARDVR_UPGRADE.BIN" "$outdir/TEST-ONLY.bin" "$outdir/DO-NOT-FLASH"

uv run -q python -I "$here/tools/base.py" "$stock" "$obj/base.bin"

CFLAGS="-mcpu=arm926ej-s -mthumb -Os -std=gnu11 -ffreestanding -fno-builtin -nostdlib
	-fno-common -ffunction-sections -fdata-sections -fno-jump-tables
	-Wall -Wextra -Werror -I$here/src"

objs=""
for src in "${srcs[@]}"; do
	o="$obj/$(basename "$src").o"
	# shellcheck disable=SC2086
	arm-none-eabi-gcc $CFLAGS -c "$src" -o "$o"
	objs="$objs $o"
done

# ld searches INCLUDE in the -L paths and the cwd only, so point it at fw.ld.
# GAP_END raises the top of the gap for emulator experiments; the default is
# the start of stock .bss.
gap_end=${GAP_END:-0xe8000}
sed -e "s|^INCLUDE fw.ld|INCLUDE \"$here/src/fw.ld\"|" -e "s|0xe8000 - 0xe6968|$gap_end - 0xe6968|" \
	"$here/src/link.ld" >"$obj/v5.ld"
# shellcheck disable=SC2086
arm-none-eabi-gcc -mcpu=arm926ej-s -mthumb -nostdlib \
	-Wl,-T,"$obj/v5.ld" -Wl,-Map,"$obj/v5.map" \
	-o "$obj/v5.elf" $objs -lgcc
# one image per region: the gap (copied by the boot block) and the overlay body
# (stored in the file padding, read from NOR by the loader)
arm-none-eabi-objcopy -O binary -j .gap "$obj/v5.elf" "$obj/gap.bin"
arm-none-eabi-objcopy -O binary -j .ovl "$obj/v5.elf" "$obj/ovl.bin"
arm-none-eabi-size -A "$obj/v5.elf"
# nm prints Thumb function values with bit 0 cleared; readelf keeps it, and both
# tools/patch.py (its ARM-symbol check on stub targets) and the emulator need the
# real state bit.
arm-none-eabi-readelf -sW "$obj/v5.elf" |
	awk 'NR > 3 && $4 ~ /^(FUNC|OBJECT|NOTYPE)$/ && $7 != "UND" && $8 !~ /^\$/ {
		print $2, $4, $8 }' | sort >"$obj/v5.sym"
# Only a build inside the real gap gets the name the card updater opens. A
# raised gap is emulator-only: other name, padded past the updater's size gate.
if [ $((gap_end)) -gt $((0xe8000)) ]; then
	out="$outdir/TEST-ONLY.bin"
else
	out="$outdir/GP_CARDVR_UPGRADE.BIN"
fi
GAP_END=$gap_end CANARY=$canary uv run -q python -I "$here/tools/patch.py" \
	"$obj/base.bin" "$obj/gap.bin" "$obj/ovl.bin" "$obj/v5.sym" "$out"
