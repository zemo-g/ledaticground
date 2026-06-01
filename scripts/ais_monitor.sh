#!/bin/bash
# ledaticground continuous AIS monitor (the "flip" — replaces the 137 noise-capture loop).
# Every cycle: capture ch-A (161.975 MHz) FM-demod on the roof Pi, pull it, decode + append
# to the timestamped vessel timeline (vessel_log.jsonl). Also logs the Pi's uptime + voltage
# throttle word each cycle so we can watch the power bank sag and clock its endurance.
set -u
GD=/Users/ledaticempire/projects/ledaticground
PI=${PI_USER:-ledatic}@${PI_HOST:-ledaticground-node}
PY=/opt/homebrew/bin/python3.11
SECS=${AIS_SECS:-45}            # capture length per cycle
CYCLE=${AIS_CYCLE:-240}         # seconds between cycle starts
MON=/tmp/ais_monitor.log
START=$(date -u +%FT%TZ)
echo "$START  ais_monitor START — capture ${SECS}s ch-A every ${CYCLE}s" >> "$MON"

while true; do
  ts=$(date +%s)
  # capture on the Pi, then report uptime + throttle (battery health) + file size
  info=$(ssh -o ConnectTimeout=10 "$PI" \
    "timeout $((SECS+3)) rtl_fm -f 161975000 -M fm -s 48000 -g 40 -l 0 /tmp/ais_mon.s16 2>/dev/null; \
     printf 'up=%ss thr=%s sz=%s' \"\$(cut -d' ' -f1 /proc/uptime)\" \"\$(vcgencmd get_throttled 2>/dev/null||echo na)\" \"\$(stat -c%s /tmp/ais_mon.s16 2>/dev/null||echo 0)\"" 2>/dev/null)
  if [ -z "$info" ]; then
    echo "$(date -u +%FT%TZ)  PI UNREACHABLE (battery dead / off-net) — endurance ends here" >> "$MON"
    sleep 60; continue
  fi
  # pull (rsync resumable, scp fallback for the weak roof WiFi)
  rsync --partial --timeout=80 -e ssh "$PI:/tmp/ais_mon.s16" /tmp/ais_mon_local.s16 2>/dev/null \
    || scp -C "$PI:/tmp/ais_mon.s16" /tmp/ais_mon_local.s16 2>/dev/null
  sz=$(wc -c </tmp/ais_mon_local.s16 2>/dev/null || echo 0)
  if [ "$sz" -gt 100000 ]; then
    res=$("$PY" "$GD/scripts/ais_census.py" --ingest /tmp/ais_mon_local.s16 "$ts" 2>&1 | tail -1)
  else
    res="pull short ($sz B) — skipped ingest"
  fi
  echo "$(date -u +%FT%TZ)  $info | $res" >> "$MON"
  sleep "$CYCLE"
done
