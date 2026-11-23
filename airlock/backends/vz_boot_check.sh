#!/usr/bin/env bash
# vz_boot_check.sh - macOS (Apple silicon) microvm feasibility check: boot a minimal Linux microVM with
# Apple Virtualization.framework through vfkit, read the guest's /proc/version, power off, and time it.
#   airlock/backends/vz_boot_check.sh [work_dir] [boots]      (default: a new temp folder, 5 boots)
# Needs no sudo and installs nothing: every download goes into work_dir and is checked against a pinned
# SHA-256 (a mismatch stops the run; the Alpine netboot kernel is replaced upstream on kernel updates,
# so a mismatch can also mean "re-pin").
# Prints one line per boot (wall time from vfkit start to VM stop) and the guest kernel string; exit 0
# only if every boot printed the marker. This is the POSITIVE control of the macOS design only: it is
# NOT the airlock agent running in a VM (see "Isolation backends" in airlock/README.md for the gap).
# Label: FINITE TEST (one host).
set -euo pipefail
[ "$(uname -s)/$(uname -m)" = Darwin/arm64 ] || { echo "vz_boot_check: needs macOS on Apple silicon" >&2; exit 2; }
W="${1:-$(mktemp -d -t forge-vz)}"; N="${2:-5}"
mkdir -p "$W/root/bin" "$W/root/proc" "$W/root/dev"
VFKIT_URL=https://github.com/crc-org/vfkit/releases/download/v0.6.4/vfkit
VFKIT_SHA=0ed83fc8ca7aa708598835480dba1362406aa7cd1dab3b27464eb76327d9652d
KERNEL_URL=https://dl-cdn.alpinelinux.org/alpine/v3.22/releases/aarch64/netboot/vmlinuz-virt
KERNEL_SHA=ebf299aa6829b15166008db24b076ac0a8b3e778c72ed66be488742aaf05d741
BB_URL=https://dl-cdn.alpinelinux.org/alpine/v3.22/main/aarch64/busybox-static-1.37.0-r20.apk
BB_SHA=ee469aee2958feffd7f64dd96655704025ff60af614f77a1d7323dc237c34da2

get() {  # get <url> <sha256> <file>
  [ -f "$3" ] || curl -fsSL -o "$3" "$1"
  echo "$2  $3" | shasum -a 256 -c - >/dev/null || { echo "vz_boot_check: SHA-256 mismatch for $1" >&2; exit 3; }
}
get "$VFKIT_URL" "$VFKIT_SHA" "$W/vfkit"; chmod +x "$W/vfkit"
get "$KERNEL_URL" "$KERNEL_SHA" "$W/vmlinuz-virt"
get "$BB_URL" "$BB_SHA" "$W/busybox.apk"
# The Alpine arm64 kernel is an EFI zboot image; Virtualization.framework's Linux boot loader needs the
# uncompressed arm64 Image inside it (header: "zimg", payload offset/size at bytes 8-15, gzip).
python3 -I - "$W/vmlinuz-virt" "$W/Image" <<'PY'
import struct, sys, zlib
b = open(sys.argv[1], "rb").read()
assert b[4:8] == b"zimg" and b[24:28] == b"gzip", "not a gzip EFI zboot kernel"
off, size = struct.unpack("<II", b[8:16])
img = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(b[off:off + size])
assert img[56:60] == b"ARMd", "payload is not an arm64 Image"
open(sys.argv[2], "wb").write(img)
PY
tar -xzf "$W/busybox.apk" -C "$W" bin/busybox.static 2>/dev/null
cp "$W/bin/busybox.static" "$W/root/bin/busybox"
printf '#!/bin/busybox sh\n/bin/busybox mount -t proc proc /proc\necho VZ-GUEST: $(/bin/busybox cat /proc/version)\necho VZ-BOOT-OK\n/bin/busybox poweroff -f\n' >"$W/root/init"
chmod +x "$W/root/init"
(cd "$W/root" && find . | cpio -o -H newc 2>/dev/null | gzip >"$W/initrd.gz")
fail=0
for i in $(seq 1 "$N"); do
  rm -f "$W/boot$i.log"
  s=$(python3 -c 'import time; print(time.monotonic())')
  "$W/vfkit" --cpus 1 --memory 256 \
    --bootloader "linux,kernel=$W/Image,initrd=$W/initrd.gz,cmdline=console=hvc0 rdinit=/init quiet" \
    --device "virtio-serial,logFilePath=$W/boot$i.log" >"$W/vfkit$i.out" 2>&1 || true
  e=$(python3 -c 'import time; print(time.monotonic())')
  ok=no; grep -aq VZ-BOOT-OK "$W/boot$i.log" 2>/dev/null && ok=yes || fail=1
  echo "vz boot $i: $(python3 -c "print(round(($e-$s)*1000))") ms marker=$ok"
done
grep -a VZ-GUEST "$W/boot1.log" | head -1 || true
echo "host: $(uname -sr)"
exit "$fail"
