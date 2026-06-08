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

# 137 antenna figure-of-merit: log this pass's 2400Hz subcarrier/noise ratio so antenna
# changes are measured automatically. Noise baseline ~1.0; a real image is >5. When the
# new antenna lands, this line jumps on its own -> the improvement is on record.
echo "=== 137 reception quality (antenna figure-of-merit) ==="
$PY "$GD/scripts/antenna_score.py" 137 /tmp/apt_shift.s16 "pass_$(date -u +%Y%m%dT%H%MZ)" 2>/dev/null || echo "  (score skipped)"
RATIO=$(tail -1 "$GD/data/antenna_scores.jsonl" 2>/dev/null | $PY -c "import sys,json
try: print(json.load(sys.stdin).get('subcarrier_2400_ratio',0))
except Exception: print(0)" 2>/dev/null); RATIO=${RATIO:-0}

echo "=== pure-Rail APT decode ==="
perl -e 'alarm 600; exec @ARGV' "$RN" run "$GD/src/apt.rail" \
  > /tmp/apt_rail_real.out 2>/tmp/apt_rail_real.err
head -1 /tmp/apt_rail_real.out
nrows=$(grep -c '^ROW' /tmp/apt_rail_real.out); echo "decoded $nrows lines"

echo "=== host sync-lock + render (real NOAA Sync-A template) ==="
$PY "$GD/scripts/apt_sync.py" /tmp/apt_rail_real.out "$GD/data/noaa15_real.png"

# HONEST SIGNAL GATE: only attest "reception" when there is actually a signal. The 2400Hz
# subcarrier ratio is calibrated so noise reads ~4-5 and a real APT image needs WELL above
# (>=6 here as a coarse floor; render+sync-lock is the definitive test). Signing a receipt
# for a pure-noise capture is a FALSE attestation of reception — honest empty state wins
# (see feedback_no_synthetic_evidence). Below the floor (or zero decoded rows), we record an
# honest no-signal marker and do NOT sign.
if awk "BEGIN{exit !($RATIO+0 >= 6)}" 2>/dev/null && [ "${nrows:-0}" -gt 0 ]; then
  echo "=== attest the real reception (2400Hz ratio $RATIO >= 6, signal present) ==="
  cp /tmp/apt_rail_real.out /tmp/apt_rail.out   # attest.rail hashes this product
  printf 'NOAA-19\n' > "$GD/data/sat_label.txt"             # honest real label (tonight's bird)
  printf 'PENDING_needs_IQ\n' > "$GD/data/doppler_residual.txt"  # image pass, no IQ
  ( cd "$RAIL" && perl -e 'alarm 120; exec @ARGV' "$RN" run "$GD/src/attest.rail" )
  echo "=== receipt ==="; cat "$GD/data/receipt.json"
  echo; echo "image -> $GD/data/noaa15_real.png"
else
  echo "=== NO SIGNAL: 2400Hz ratio $RATIO < 6 (nrows=${nrows:-0}) — NOT attesting (honest no-signal) ==="
  printf '{"status":"no_signal","band":"137","subcarrier_2400_ratio":%s,"nrows":%s,"ts":"%s"}\n' \
    "$RATIO" "${nrows:-0}" "$(date -u +%FT%TZ)" > "$GD/data/last_no_signal.json"
  echo "wrote honest no-signal marker -> $GD/data/last_no_signal.json"
fi
