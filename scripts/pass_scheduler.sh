#!/bin/bash
# ledaticground — SATELLITE PASS SCHEDULER (time-share the single radio).
#
# Policy (set by Reilly 2026-06-07): VESSELS (162 AIS via roofmon.service) are the
# always-on MAIN system. Satellites (NOAA APT, and any other birds we can decode)
# are SECONDARY — captured only in their allotted pass windows. This script preempts
# the radio ONLY for the pass duration, then ALWAYS returns it to AIS.
#
#   pass_scheduler.sh           # loop forever (deploy as LaunchAgent)
#   pass_scheduler.sh --once    # capture the next pass, then exit
#
# Safety: roofmon (the main system) is resumed on every exit path with retries; a
# separate Pi-side deadman (install_roofmon_deadman) is the independent backstop.
set -u
GD=/Users/ledaticempire/projects/ledaticground
PI_HOST=${PI_HOST:-100.115.30.12}; PI_USER=${PI_USER:-ledatic}    # roofv2
PI="${PI_USER}@${PI_HOST}"
SSH="ssh -o ConnectTimeout=10 -o BatchMode=yes"
PY=/opt/homebrew/bin/python3.11
MINEL=${MINEL:-20}
# Approximate station location (Detroit Salsa Co) for satellite ground-track geometry
# ONLY. The signed reception receipt keeps geo=PENDING (no GPS) — we do not attest a
# precise location we cannot prove.
NODE_LAT=${NODE_LAT:-42.31}; NODE_LON=${NODE_LON:--83.08}
ONCE=0; [ "${1:-}" = "--once" ] && ONCE=1

log(){ echo "$(date -u +%FT%TZ) $*"; }

resume_ais(){           # ALWAYS bring the MAIN system (AIS) back; retry hard.
  local a
  for i in 1 2 3 4 5; do
    $SSH "$PI" "sudo systemctl start roofmon.service" 2>/dev/null
    a=$($SSH "$PI" "systemctl is-active roofmon.service" 2>/dev/null)
    [ "$a" = "active" ] && { log "AIS main system resumed (roofmon active)"; return 0; }
    sleep 5
  done
  log "!! WARNING: roofmon not confirmed active — Pi-side deadman should recover it"
  return 1
}

capture_pass(){         # $1=sat $2=freq(Hz) $3=dur(min)
  local SAT="$1" FREQ="$2" DUR="$3" RDUR=$(( ($3+3)*60 )) rf WSKIP loc
  log "PASS WINDOW: $SAT @ ${FREQ}Hz ~${DUR}min — preempting AIS (allotted satellite slot)"
  $SSH "$PI" "sudo systemctl stop roofmon.service" || { log "could not stop roofmon; abort"; return 1; }
  sleep 1
  if ! $SSH "$PI" "nohup /usr/local/bin/pi_record.sh $FREQ $RDUR pass >/tmp/recout 2>&1 &"; then
    log "Pi capture trigger failed"; resume_ais; return 1
  fi
  log "recording ${RDUR}s on the Pi..."
  sleep $(( RDUR + 30 ))
  resume_ais            # free the radio + restore the MAIN system ASAP; decode is off-radio
  rf=$($SSH "$PI" "ls -t /tmp/cap_pass_*.s16 2>/dev/null | head -1")
  [ -n "$rf" ] || { log "no recording landed on Pi"; return 1; }
  WSKIP=$(( DUR*30 - 30 )); [ "$WSKIP" -lt 0 ] && WSKIP=0   # high-elevation center window
  $SSH "$PI" "dd if='$rf' of=/tmp/pass_win.s16 bs=22050 skip=$WSKIP count=120 2>/dev/null"
  loc=/tmp/incoming_pass.s16
  scp -C "$PI:/tmp/pass_win.s16" "$loc" 2>/dev/null
  log "decoding $(basename "$rf") ($(wc -c <"$loc" 2>/dev/null) bytes) -> APT image + receipt"
  bash "$GD/scripts/recv_decode.sh" "$loc" roofv2 "$NODE_LAT" "$NODE_LON" 0 110 || log "decode returned nonzero"
}

run_next(){
  local info; info=$($PY "$GD/scripts/next_pass.py" --minel "$MINEL")
  if [[ "$info" == NONE* ]]; then log "no pass >= ${MINEL}deg soon"; return 2; fi
  eval "$info"   # SAT MINS DUR ELEV FREQ AOS_EPOCH
  log "next: $SAT in ${MINS}min  El${ELEV}deg  @${FREQ}Hz  (AOS $(date -u -r $AOS_EPOCH +%H:%MZ 2>/dev/null || echo +${MINS}min))"
  local now w; now=$(date +%s); w=$(( AOS_EPOCH - now - 45 ))
  if [ "$w" -gt 0 ]; then log "AIS keeps running; sleeping ${w}s until AOS-45s"; sleep "$w"; fi
  capture_pass "$SAT" "$FREQ" "$DUR"
}

if [ "$ONCE" = 1 ]; then run_next; else while true; do run_next || sleep 1800; done; fi
