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
CAPTURE=${CAPTURE:-apt}      # apt = FM-demod audio -> APT decode (NOAA only); iq = raw IQ -> waterfall+decode (NOAA+Meteor)
GAIN=${GAIN:-49}             # rtl gain for raw-IQ capture (49 = max sensitivity / diagnostic baseline)
RAWDIR=${RAWDIR:-/Users/ledaticempire/.ledatic/roofv2/raw_iq}   # where pulled raw-IQ artifacts land
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

capture_iq_pass(){      # $1=sat $2=freq(Hz) $3=dur(min) $4=elev $5=mode — RAW IQ: the ground-truth, externally-validatable artifact
  local SAT="$1" FREQ="$2" DUR="$3" ELEV="${4:-0}" MODE="${5:-APT}"
  local RDUR=$(( ($3+3)*60 )) ts label pf sz loc out
  ts=$(date -u +%Y%m%dT%H%MZ); label="iq_${SAT// /}_el${ELEV}_${MODE}_${ts}"; pf="/home/ledatic/${label}.bin"
  mkdir -p "$RAWDIR"
  log "IQ PASS WINDOW: $SAT $MODE El${ELEV} @${FREQ}Hz ~${DUR}min — preempting AIS for raw-IQ capture (g${GAIN})"
  $SSH "$PI" "sudo systemctl stop roofmon.service" || { log "could not stop roofmon; abort"; return 1; }
  sleep 1
  # raw uint8 I/Q @250k; timeout-bounded so the SDR ALWAYS frees even if rtl_sdr hangs -> AIS can resume.
  if ! $SSH "$PI" "nohup timeout -k 10 $RDUR rtl_sdr -f $FREQ -s 250000 -g $GAIN '$pf' >/tmp/iqrec.log 2>&1 &"; then
    log "Pi IQ capture trigger failed"; resume_ais; return 1
  fi
  log "raw-IQ recording ${RDUR}s on the Pi (~$(( RDUR / 2 ))MB @250k)..."
  sleep $(( RDUR + 20 ))
  resume_ais            # free the radio + restore the MAIN system ASAP; pull + decode are off-radio
  sz=$($SSH "$PI" "stat -c%s '$pf' 2>/dev/null" || echo 0); sz=${sz:-0}
  if [ "$sz" -lt 1000000 ]; then log "IQ capture too small (${sz}B) — capture failed, nothing to pull"; return 1; fi
  loc="$RAWDIR/${label}.bin"
  log "captured ${sz}B; pulling -> $(basename "$loc")"
  scp -C "$PI:$pf" "$loc" 2>/dev/null || { log "scp failed (IQ left on Pi at $pf)"; return 1; }
  $SSH "$PI" "rm -f '$pf'"
  out=$("$PY" "$GD/scripts/iq_apt_decode.py" "$loc" "${loc%.bin}" 2>&1) || log "iq_apt_decode returned nonzero"
  log "iq_decode: $(echo "$out" | tr '\n' '|')"
  # independent cross-check: satdump (reference decoder) on the SAME bytes — non-fatal, off-radio.
  # outputs-match between our decoder and satdump = the external proof (raw-IQ-first thesis).
  bash "$GD/scripts/validate_external.sh" "$loc" >/tmp/iqval.log 2>&1 || true
  # honest signal tell from satdump's OWN decode OUTPUT (not module names — those appear on noise too):
  #   LRPT -> max MSU-MR image line count (>0 = real frames decoded); APT -> wedge calibration succeeded.
  xl=$(grep -oE "Lines  : [0-9]+" /tmp/iqval.log 2>/dev/null | grep -oE "[0-9]+$" | sort -rn | head -1)
  if [ -n "$xl" ]; then [ "$xl" -gt 0 ] && xtag="[satdump LRPT lines=$xl -> SIGNAL]" || xtag="[satdump LRPT lines=0 -> noise]"
  elif grep -qiE "no valid wedge|Couldn.t calibrate" /tmp/iqval.log; then xtag="[satdump APT no-wedge -> noise]"
  else xtag="[satdump APT wedge-ok -> SIGNAL?]"; fi
  log "satdump xcheck -> ${loc%.bin}.satdump/ $xtag"
}

run_next(){
  # keep TLEs fresh so skyfield timing doesn't drift; refresh if >12h old (radio-free, at loop top).
  # fetch_tle.sh is set -e + writes via temp->cat, so a failed curl leaves the existing TLE intact.
  local tlef="$GD/data/tle_weather.txt" tage
  tage=$(( $(date +%s) - $(stat -f %m "$tlef" 2>/dev/null || echo 0) ))
  if [ "$tage" -gt 43200 ]; then
    if bash "$GD/scripts/fetch_tle.sh" >/tmp/tle_fetch.log 2>&1; then log "TLE refreshed: $(tail -1 /tmp/tle_fetch.log)"; else log "TLE refresh failed — keeping existing (age $((tage/3600))h)"; fi
  fi
  local na="--minel $MINEL"; [ "$CAPTURE" = iq ] && na="$na --all"   # iq mode also catches Meteor (raw IQ decodes any carrier)
  local info; info=$($PY "$GD/scripts/next_pass.py" $na)
  if [[ "$info" == NONE* ]]; then log "no pass >= ${MINEL}deg soon"; return 2; fi
  eval "$info"   # SAT MINS DUR ELEV FREQ MODE AOS_EPOCH
  log "next: $SAT (${MODE:-APT}) in ${MINS}min  El${ELEV}deg  @${FREQ}Hz  [$CAPTURE]  (AOS $(date -u -r $AOS_EPOCH +%H:%MZ 2>/dev/null || echo +${MINS}min))"
  local now w; now=$(date +%s); w=$(( AOS_EPOCH - now - 45 ))
  if [ "$w" -gt 0 ]; then log "AIS keeps running; sleeping ${w}s until AOS-45s"; sleep "$w"; fi
  if [ "$CAPTURE" = iq ]; then capture_iq_pass "$SAT" "$FREQ" "$DUR" "$ELEV" "${MODE:-APT}"; else capture_pass "$SAT" "$FREQ" "$DUR"; fi
}

if [ "$ONCE" = 1 ]; then run_next; else while true; do run_next || sleep 1800; done; fi
