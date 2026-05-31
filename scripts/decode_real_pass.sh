#!/bin/bash
# Decode + attest the REAL NOAA 15 APT capture (the climax).
# Windows the high-elevation center of the pass for the cleanest image, runs the
# pure-Rail APT decoder, host-side sync-locks + renders a PNG, then signs a
# proof-of-reception receipt. Run after /tmp/noaa15_apt.s16 lands (~00:09 UTC).
#   usage: decode_real_pass.sh [skip_sec] [len_sec]   (default 270 420 = el>40deg)
set -u
RN=/Users/ledaticempire/projects/rail/rail_native
RAIL=/Users/ledaticempire/projects/rail
GD=/Users/ledaticempire/projects/ledaticground
PY=/opt/homebrew/bin/python3.11
SRC=/tmp/noaa15_apt.s16
SKIP=${1:-270}      # skip first 4.5 min (low-elevation rise)
LEN=${2:-420}       # decode 7 min around the 81-deg peak
BPS=22050           # 11025 Hz * 2 bytes (s16 mono)

[ -f "$SRC" ] || { echo "NO CAPTURE at $SRC"; exit 1; }
bytes=$(wc -c <"$SRC"); secs=$((bytes/BPS))
echo "capture: $bytes bytes (~${secs}s).  windowing skip=${SKIP}s len=${LEN}s"

# preserve the synthetic fixture, then stage the windowed real capture where
# apt.rail reads (it is hardcoded to /tmp/apt_shift.s16; selftest doesn't use it)
cp -n /tmp/apt_shift.s16 /tmp/apt_shift_synth.bak 2>/dev/null || true
dd if="$SRC" of=/tmp/apt_shift.s16 bs=$BPS skip=$SKIP count=$LEN 2>/dev/null
echo "staged $(wc -c </tmp/apt_shift.s16) bytes -> /tmp/apt_shift.s16"

echo "=== pure-Rail APT decode ==="
perl -e 'alarm 600; exec @ARGV' "$RN" run "$GD/src/apt.rail" \
  > /tmp/apt_rail_real.out 2>/tmp/apt_rail_real.err
head -1 /tmp/apt_rail_real.out
nrows=$(grep -c '^ROW' /tmp/apt_rail_real.out); echo "decoded $nrows lines"

echo "=== host sync-lock + render (real NOAA Sync-A template) ==="
$PY "$GD/scripts/apt_sync.py" /tmp/apt_rail_real.out "$GD/data/noaa15_real.png"

echo "=== attest the real reception ==="
cp /tmp/apt_rail_real.out /tmp/apt_rail.out   # attest.rail hashes this product
( cd "$RAIL" && perl -e 'alarm 120; exec @ARGV' "$RN" run "$GD/src/attest.rail" )
echo "=== receipt ==="; cat "$GD/data/receipt.json"
echo; echo "image -> $GD/data/noaa15_real.png"
