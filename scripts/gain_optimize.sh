#!/bin/bash
# ledaticground RTL-SDR gain optimizer: sweep tuner gain at 162 MHz, score each, and
# recommend the operating point — highest decode count / SNR without driving clipping up.
# Each gain = one short capture from the roof Pi, so this is capture-heavy over the weak
# roof WiFi; run it when you're settling an antenna, not continuously.
#
#   gain_optimize.sh [secs] [gains...]      # default secs=6, gains "20 30 40 49"
set -u
GD=/Users/ledaticempire/projects/ledaticground
SECS=${1:-6}; shift 2>/dev/null || true
GAINS=${*:-"20 30 40 49"}
PY=/opt/homebrew/bin/python3.11
LOG="$GD/data/antenna_scores.jsonl"

echo "gain sweep @162.0MHz, ${SECS}s each: $GAINS"
for g in $GAINS; do
  echo "--- gain $g ---"
  bash "$GD/scripts/antenna_score.sh" 162 "gainsweep_g${g}" "$SECS" "$g" 2>&1 | grep -E "snr_a_db|snr_b_db|clip_pct|bursts_decoded|unique_mmsi" | sed 's/[",]//g; s/^/  /'
done

echo
echo "=== sweep summary (from $(basename "$LOG")) ==="
$PY - "$LOG" <<'PY'
import sys, json
rows = [json.loads(l) for l in open(sys.argv[1]) if '"gainsweep_g' in l]
rows = {r["label"]: r for r in rows}.values()   # last of each gain
rows = sorted(rows, key=lambda r: int(r["label"].split("_g")[1]))
print(f"{'gain':>5} {'snrA':>6} {'snrB':>6} {'clip%':>6} {'decoded':>8} {'mmsi':>5}")
best = None
for r in rows:
    g = int(r["label"].split("_g")[1])
    print(f"{g:>5} {r['snr_a_db']:>6} {r['snr_b_db']:>6} {r['clip_pct']:>6} "
          f"{r['bursts_decoded']:>8} {r['unique_mmsi']:>5}")
    # prefer max decoded, tie-break max SNR, reject clipping >1%
    key = (r['bursts_decoded'], r['snr_a_db']) if r['clip_pct'] < 1.0 else (-1, -99)
    if best is None or key > best[0]:
        best = (key, g)
if best: print(f"\nRECOMMEND gain={best[1]} (most decodes, clip<1%)")
PY
