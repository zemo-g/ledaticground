#!/bin/bash
# Process the REAL NOAA-19 Doppler capture into an attested proof-of-reception.
# Runs after /tmp/dopcap/done lands (~03:20 UTC):
#   1. concatenate the IQ snapshots in time order
#   2. pure-Rail centroid Doppler track (doppler_real.rail)
#   3. SGP4-predicted Doppler over the same pass (doppler_predict.rail)
#   4. fit measured vs predicted (best time-shift + constant offset) -> corr, residual
#   5. sign a receipt binding the real measured residual (attest.rail)
set -u
RN=/Users/ledaticempire/projects/rail/rail_native
RAIL=/Users/ledaticempire/projects/rail
GD=/Users/ledaticempire/projects/ledaticground
PY=/opt/homebrew/bin/python3.11
D=/tmp/dopcap
[ -f "$D/done" ] || { echo "capture not done ($D/done missing)"; exit 1; }
ns=$(ls "$D"/snap_*.iq 2>/dev/null | wc -l | tr -d ' ')
echo "snapshots: $ns"; [ "$ns" -gt 10 ] || { echo "too few snapshots"; exit 1; }

echo "=== concat snapshots in time order -> /tmp/dop_real.iq ==="
ls "$D"/snap_*.iq | sort | xargs cat > /tmp/dop_real.iq
wc -c /tmp/dop_real.iq

echo "=== pure-Rail centroid Doppler track ==="
$RN src/doppler_real.rail 2>/tmp/dr.err
perl -e 'alarm 300; exec @ARGV' /tmp/rail_out > /tmp/dop_meas_real.out 2>/dev/null
head -1 /tmp/dop_meas_real.out; echo "DOP lines: $(grep -c '^DOP' /tmp/dop_meas_real.out)"

echo "=== predicted Doppler (set now to first snapshot time so the pass is upcoming) ==="
head -1 "$D/times.txt" | awk '{print $2 - 120}' > "$GD/data/now_unix.txt"   # 2 min before AOS
( cd "$GD" && $RN run src/doppler_predict.rail ) > /tmp/dop_pred.out 2>/dev/null
echo "predicted points: $(grep -c '^DOPPLER' /tmp/dop_pred.out)"

echo "=== fit measured vs predicted ==="
$PY scripts/doppler_fit.py /tmp/dop_meas_real.out --predict /tmp/dop_pred.out --times "$D/times.txt" | tee /tmp/dop_fit.out

echo "=== attest the real Doppler-bound reception ==="
cp /tmp/dop_meas_real.out /tmp/apt_rail.out      # product = the measured track
( cd "$RAIL" && perl -e 'alarm 120; exec @ARGV' "$RN" run "$GD/src/attest.rail" )
echo "=== receipt ==="; cat "$GD/data/receipt.json"
