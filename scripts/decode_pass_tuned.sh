#!/bin/bash
# decode_pass_tuned.sh — PARALLEL "tuned" decode of a satellite pass. Runs AFTER the
# scheduler's official auto-decode, never concurrently.
#
# WHY: capture_pass() decodes only a ~110s CENTER window. This pulls the FULL recording,
# scores several windows for the strongest 2400Hz APT subcarrier, and re-decodes a WIDER
# window (default 300s) centered on the best segment -> a taller, cleaner image.
#
# SAFETY (hard rules so it cannot harm tonight's official result):
#   * IMAGE-ONLY — never runs attest.rail, never writes data/receipt.json or any prod
#     sign surface. (See feedback_manual_run_signs_real_chain.) Output is a separate PNG.
#   * apt.rail is hardcoded to /tmp/apt_shift.s16 and the official run shares it, so we
#     GUARD: only proceed once data/receipt.json is fresh (official decode confirmed done),
#     then it's safe to reuse /tmp/apt_shift.s16. Tuned rail output goes to a separate file.
#   * Read-only on the Pi (one scp of the finished recording); never touches the SDR.
set -u
RN=/Users/ledaticempire/projects/rail/rail_native
GD=/Users/ledaticempire/projects/ledaticground
PY=/opt/homebrew/bin/python3.11
PI=${PI:-ledatic@100.115.30.12}
SSH="ssh -o ConnectTimeout=10 -o BatchMode=yes"
BPS=22050                                   # 11025 Hz * 2 bytes (s16 mono)
WINLEN=${WINLEN:-300}                        # tuned decode window (s); auto-decode uses ~110
TUNED_PNG="$GD/data/noaa19_tuned.png"
RC="$GD/data/receipt.json"

log(){ echo "TUNED: $*"; }

# ---- GUARD: official auto-decode must be DONE (fresh receipt) before we touch /tmp/apt_shift.s16
mt=$(stat -f %m "$RC" 2>/dev/null || echo 0); age=$(( $(date +%s) - mt ))
if [ "$mt" -eq 0 ] || [ "$age" -gt 1200 ] || [ "$age" -lt 0 ]; then
  log "official receipt not fresh (age=${age}s) — auto-decode not confirmed done; SKIP (won't race /tmp/apt_shift.s16)"
  exit 2
fi

# ---- pull the FULL recording from the Pi (read-only)
RF=$($SSH "$PI" 'ls -t /tmp/cap_pass_*.s16 2>/dev/null | head -1' 2>/dev/null)
[ -n "$RF" ] || { log "no /tmp/cap_pass_*.s16 on Pi"; exit 1; }
scp -C -o ConnectTimeout=20 -o BatchMode=yes "$PI:$RF" /tmp/cap_pass_full.s16 2>/dev/null || { log "scp failed"; exit 1; }
bytes=$(wc -c </tmp/cap_pass_full.s16); secs=$(( bytes / BPS ))
log "full pass $(basename "$RF") = $bytes bytes (~${secs}s)"
[ "$secs" -lt $(( WINLEN + 60 )) ] && { log "recording too short (${secs}s)"; exit 1; }

# ---- multi-window SNR scan (180s windows) to find the strongest 2400Hz subcarrier
best_sk=-1; best_ratio=0
for sk in $(seq 30 90 $(( secs - 180 ))); do
  dd if=/tmp/cap_pass_full.s16 of=/tmp/tuned_win.s16 bs=$BPS skip=$sk count=180 2>/dev/null
  # antenna_score prints JSON + a trailing "logged to" line (un-parseable as one blob); read
  # the compact record it APPENDS to antenna_scores.jsonl instead.
  $PY "$GD/scripts/antenna_score.py" 137 /tmp/tuned_win.s16 "tuned_win_+${sk}s" >/dev/null 2>&1
  r=$(tail -1 "$GD/data/antenna_scores.jsonl" | $PY -c "import sys,json; print(json.load(sys.stdin).get('subcarrier_2400_ratio',0))" 2>/dev/null)
  [ -z "$r" ] && r=0
  echo "  window +${sk}s : 2400Hz ratio = $r"
  awk "BEGIN{exit !($r > $best_ratio)}" 2>/dev/null && { best_ratio=$r; best_sk=$sk; }
done
log "best window = +${best_sk}s  (ratio ${best_ratio};  >12 likely-signal / 6-12 inconclusive / <6 noise)"
[ "$best_sk" -lt 0 ] && { log "no scorable window"; exit 1; }

# ---- decode a WIDER window centered on the best segment (image-only)
WS=$(( best_sk - (WINLEN-180)/2 )); [ "$WS" -lt 0 ] && WS=0
WL=$WINLEN; [ $(( WS + WL )) -gt "$secs" ] && WL=$(( secs - WS ))
dd if=/tmp/cap_pass_full.s16 of=/tmp/apt_shift.s16 bs=$BPS skip=$WS count=$WL 2>/dev/null
log "decoding skip=${WS}s len=${WL}s (vs auto ~110s) -> ${WL} s ~ $(( WL*2 )) lines"
perl -e 'alarm 600; exec @ARGV' "$RN" run "$GD/src/apt.rail" > /tmp/apt_rail_tuned.out 2>/tmp/apt_rail_tuned.err
nrows=$(grep -c '^ROW' /tmp/apt_rail_tuned.out 2>/dev/null || echo 0)
log "rail APT decoded $nrows rows"
[ "$nrows" -gt 0 ] || { log "decode produced no rows"; exit 1; }
$PY "$GD/scripts/apt_sync.py" /tmp/apt_rail_tuned.out "$TUNED_PNG" 2>&1 | tail -3
log "IMAGE -> $TUNED_PNG  (image-only, NOT attested — official receipt is the auto-decode's)"
