#!/bin/bash
# ledaticground PI-SIDE antenna wiggle probe — live ~18s-cycle feedback while
# physically adjusting the roof antenna. Extends antune.sh's one-shot metric
# (in-band floor median MINUS just-out-of-band reference, which rejects
# gain/PSU common-mode shifts) into a detached loop that time-shares the radio
# safely with the capture services.
#
# WHY DETACHED (nohup, not a held ssh loop): roof WiFi drops; a held session
# dying mid-wiggle must not leave services stopped or lose the meter history.
# The loop appends to ~/roof_data/wiggle.{jsonl,log} regardless of who is
# watching; the Mini-side wrapper (scripts/wiggle.sh) just tails the log.
#
# SAFETY — PASS-GUARD: every cycle reads ~/.iq/schedule.tsv (AOS_EPOCH first
# field). If the next pass is < GUARD seconds away (default 420), the probe
# restarts roofmon + iqcap and exits, so a wiggle session can NEVER eat a
# scheduled capture even if the Mini is unreachable and nobody types stop.
#
#   wiggle_probe.sh start [gain]   # stop services, launch detached loop
#   wiggle_probe.sh stop           # kill loop, restart services
#   wiggle_probe.sh status         # pidfile + services + last reading
#
# METRICS per cycle (fixed gain, default 40 — comparable run-to-run AND to
# historical antune.sh readings):
#   sat137  : median floor 136-138 MHz minus ref 138.5-140 MHz   } MAXIMIZE
#   ais162  : median floor 161.5-162.5 MHz minus ref 158-160 MHz } both
# Raw medians are logged too so absolute floor shifts stay visible.
set -u
GAIN_FILE=/tmp/wiggle_gain
PIDFILE=/tmp/wiggle_probe.pid
OUT_JSON=$HOME/roof_data/wiggle.jsonl
OUT_LOG=$HOME/roof_data/wiggle.log
SCHED=$HOME/.iq/schedule.tsv
GUARD=${WIGGLE_GUARD:-420}
SERVICES="roofmon.service ledaticground-iqcap.service"

services_start() { sudo systemctl start $SERVICES 2>/dev/null; }
services_stop()  { sudo systemctl stop  $SERVICES 2>/dev/null; }

next_aos() {  # earliest schedule AOS_EPOCH still in the future, or empty
    local now; now=$(date +%s)
    awk -F'\t' -v now="$now" '$1 > now {print $1; exit}' "$SCHED" 2>/dev/null
}

sweep() {  # sweep <lo> <hi> <bin> -> rtl_power csv on stdout (empty on failure)
    rtl_power -f "$1:$2:$3" -g "$(cat "$GAIN_FILE" 2>/dev/null || echo 40)" -i 2 -1 /tmp/wiggle_sweep.csv 2>/dev/null \
        && cat /tmp/wiggle_sweep.csv
}

loop() {
    local n=0
    echo "$(date -u +%FT%TZ) wiggle loop START gain=$(cat "$GAIN_FILE") guard=${GUARD}s" >> "$OUT_LOG"
    while :; do
        # --- pass-guard: restore services + exit before any scheduled AOS ---
        local aos; aos=$(next_aos)
        if [ -n "${aos:-}" ] && [ $((aos - $(date +%s))) -lt "$GUARD" ]; then
            echo "$(date -u +%FT%TZ) PASS-GUARD: AOS in $((aos - $(date +%s)))s — restoring services, exiting" >> "$OUT_LOG"
            services_start
            rm -f "$PIDFILE"
            exit 0
        fi
        sweep 134M 140M 25k  > /tmp/wiggle_137.csv
        sweep 156M 163M 50k  > /tmp/wiggle_162.csv
        n=$((n+1))
        N=$n python3 - "$OUT_JSON" "$OUT_LOG" <<'PY'
import csv, json, os, statistics, sys, time

def bins(path):
    out = []
    try:
        for row in csv.reader(open(path)):
            if len(row) < 7: continue
            try:
                lo = float(row[2]); step = float(row[4])
                dbs = [float(x) for x in row[6:]]
            except ValueError:
                continue
            for i, db in enumerate(dbs):
                if db == db: out.append((lo + i*step, db))
    except OSError:
        pass
    return out

def med(b, a, z):
    v = [db for f, db in b if a <= f < z]
    return round(statistics.median(v), 2) if v else None

b137 = bins('/tmp/wiggle_137.csv'); b162 = bins('/tmp/wiggle_162.csv')
sat_in, sat_ref = med(b137, 136e6, 138e6), med(b137, 138.5e6, 140e6)
ais_in, ais_ref = med(b162, 161.5e6, 162.5e6), med(b162, 158e6, 160e6)
rec = {
    'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    'n': int(os.environ['N']),
    'sat137_in': sat_in, 'sat137_ref': sat_ref,
    'sat137_delta': round(sat_in - sat_ref, 2) if None not in (sat_in, sat_ref) else None,
    'ais162_in': ais_in, 'ais162_ref': ais_ref,
    'ais162_delta': round(ais_in - ais_ref, 2) if None not in (ais_in, ais_ref) else None,
}
with open(sys.argv[1], 'a') as f:
    f.write(json.dumps(rec) + '\n')
sd, ad = rec['sat137_delta'], rec['ais162_delta']
line = (f"{rec['ts']} #{rec['n']:>3}  137delta={sd if sd is not None else ' n/a'}dB"
        f" (in {sat_in} ref {sat_ref})   162delta={ad if ad is not None else ' n/a'}dB"
        f" (in {ais_in} ref {ais_ref})")
with open(sys.argv[2], 'a') as f:
    f.write(line + '\n')
PY
    done
}

case "${1:-status}" in
  start)
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null && { echo "already running pid=$(cat "$PIDFILE")"; exit 1; }
    aos=$(next_aos)
    if [ -n "${aos:-}" ] && [ $((aos - $(date +%s))) -lt "$GUARD" ]; then
        echo "REFUSED: next pass AOS in $((aos - $(date +%s)))s (< ${GUARD}s guard)"; exit 1
    fi
    echo "${2:-40}" > "$GAIN_FILE"
    services_stop
    sleep 2   # let rtl device release
    nohup "$0" __loop >/dev/null 2>&1 &
    echo $! > "$PIDFILE"
    [ -n "${aos:-}" ] && echo "started pid=$! gain=${2:-40} — next pass in $(( (aos - $(date +%s)) / 60 ))min (auto-restore at T-$((GUARD/60))min)" \
                      || echo "started pid=$! gain=${2:-40} — no future pass in schedule"
    ;;
  __loop) loop ;;
  stop)
    [ -f "$PIDFILE" ] && { kill "$(cat "$PIDFILE")" 2>/dev/null; rm -f "$PIDFILE"; echo "loop killed"; } || echo "no pidfile"
    services_start
    echo "services restarted: $(systemctl is-active $SERVICES | tr '\n' ' ')"
    ;;
  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then echo "probe RUNNING pid=$(cat "$PIDFILE")"; else echo "probe not running"; fi
    echo "services: $(systemctl is-active $SERVICES 2>/dev/null | tr '\n' ' ')"
    tail -1 "$OUT_LOG" 2>/dev/null
    ;;
  *) echo "usage: wiggle_probe.sh start [gain] | stop | status"; exit 1 ;;
esac
