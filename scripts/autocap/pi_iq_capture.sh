#!/bin/bash
# Pi-LOCAL autonomous satellite capturer (decoupled from the Mini).
#
# Reads a Mini-pushed schedule (~/.iq/schedule.tsv) and captures each pass to local
# disk on the Pi's OWN clock — NO WiFi needed at capture time. Keeps every file; the
# Mini pulls opportunistically. Idempotent across reboots (AOS-keyed manifest).
#
# AIS (roofmon.service) is the MAIN system: a capture preempts it ONLY for the pass
# window and ALWAYS restarts it (>=6x retry + SIGTERM trap + the Pi-side deadman cron).
#
# Schedule row (TAB-separated, from enum_passes.py): AOS_EPOCH DUR_MIN ELEV FREQ_HZ MODE SAT
# Capture file (EXACT, matches pass_scheduler.sh so iq_apt_decode.py + validate_external.sh work):
#   iq_<SATNOSPACES>_el<EL>_<MODE>_<YYYYMMDDTHHMMZ>.bin   (uppercase T and Z)
# Atomic publish: write <name>.bin.tmp, mv -> <name>.bin only after rtl_sdr exits, so the
# Mini puller can never rsync a half-written capture.
#
# DRY_RUN=1 -> log intended actions without touching the SDR or roofmon (for safe testing).
set -u
IQ="${IQ_DIR:-$HOME/.iq}"
SCHED="$IQ/schedule.tsv"
CAPS="$IQ/captures"
MAN="$IQ/manifest.tsv"          # Pi-internal idempotency: AOS_EPOCH<TAB>name<TAB>bytes<TAB>captured_epoch
GAIN="${GAIN:-49}"
SR=250000
DRY="${DRY_RUN:-0}"
mkdir -p "$CAPS"

log(){ echo "$(date -u +%FT%TZ) [iqcap] $*"; }

resume_roofmon(){               # ALWAYS bring AIS back; retry hard
  local i a
  for i in 1 2 3 4 5 6; do
    [ "$DRY" = 1 ] && { log "DRY: would start roofmon"; return 0; }
    sudo systemctl start roofmon.service 2>/dev/null
    a=$(systemctl is-active roofmon.service 2>/dev/null)
    [ "$a" = active ] && { log "roofmon active (AIS resumed)"; return 0; }
    sleep 5
  done
  log "!! WARNING roofmon not confirmed active — Pi-side deadman should recover it"; return 1
}

# If stopped/killed mid-capture, free the SDR and restore AIS before exiting.
trap 'log "signal -> free SDR + restore AIS"; [ "$DRY" = 1 ] || pkill -f "rtl_sdr -f" 2>/dev/null; resume_roofmon; exit 143' TERM INT

captured(){                     # idempotency by AOS_EPOCH (survives reboot via the manifest file)
  [ -f "$MAN" ] && grep -q "^$1	" "$MAN"
}

do_capture(){                   # $1=aos $2=dur $3=el $4=freq $5=mode $6=sat
  local aos="$1" dur="$2" el="$3" freq="$4" mode="$5" sat="$6"
  local satns ts name out tmp rdur sz rc
  satns=$(printf '%s' "$sat" | tr -d ' ')
  ts=$(date -u -d "@$aos" +%Y%m%dT%H%MZ 2>/dev/null || date -u +%Y%m%dT%H%MZ)
  name="iq_${satns}_el${el}_${mode}_${ts}.bin"
  out="$CAPS/$name"; tmp="$out.tmp"
  rdur=$(( (dur + 3) * 60 ))
  log "PASS $sat $mode el${el} @${freq}Hz ~${dur}min -> $name (window ${rdur}s, g${GAIN})"
  if [ "$DRY" = 1 ]; then
    log "DRY: stop roofmon; timeout -k 10 $rdur rtl_sdr -f $freq -s $SR -g $GAIN $tmp; mv -> $out; manifest += $aos"
    return 0
  fi
  sudo systemctl stop roofmon.service || { log "could not stop roofmon; abort pass"; return 1; }
  sleep 1
  timeout -k 10 "$rdur" rtl_sdr -f "$freq" -s "$SR" -g "$GAIN" "$tmp" >/tmp/iqcap_rtl.log 2>&1
  rc=$?
  resume_roofmon                # free SDR + restore AIS ASAP, regardless of capture outcome
  sz=$(stat -c%s "$tmp" 2>/dev/null || echo 0)
  if [ "${sz:-0}" -lt 1000000 ]; then log "capture too small (${sz}B rc=$rc) — discarding $tmp"; rm -f "$tmp"; return 1; fi
  mv -f "$tmp" "$out"           # atomic publish: pull only ever sees the complete final name
  printf '%s\t%s\t%s\t%s\n' "$aos" "$name" "$sz" "$(date +%s)" >> "$MAN"
  log "captured $name (${sz}B), manifest updated (rc=$rc)"
}

log "iqcap up (IQ=$IQ DRY=$DRY)"
while true; do
  if [ ! -s "$SCHED" ]; then log "no schedule yet ($SCHED); sleep 300"; sleep 300; continue; fi
  now=$(date +%s); found=0
  # soonest FUTURE-or-just-now (within 120s grace) pass not already captured
  while IFS=$'\t' read -r aos dur el freq mode sat; do
    case "$aos" in ''|'#'*) continue;; esac          # skip blanks / header / comments
    [ "$dur" -ge 0 ] 2>/dev/null || { log "skip malformed row: $aos $dur"; continue; }
    captured "$aos" && continue                       # already done (power-cycle safe)
    if [ "$aos" -gt "$(( now - 120 ))" ]; then found=1; break; fi
  done < "$SCHED"               # redirect (not pipe) -> vars persist after break
  if [ "$found" != 1 ]; then log "no upcoming uncaptured pass; sleep 300"; sleep 300; continue; fi

  wait=$(( aos - 45 - $(date +%s) ))
  if [ "$wait" -gt 0 ]; then
    [ "$wait" -gt 300 ] && wait=300                   # cap the nap so a freshly-pushed earlier pass is seen
    log "next $sat in $(( (aos - $(date +%s)) / 60 ))min (AOS $(date -u -d @"$aos" +%H:%MZ)); AIS up, sleep ${wait}s"
    sleep "$wait"; continue
  fi
  do_capture "$aos" "$dur" "$el" "$freq" "$mode" "$sat" || { log "capture failed; backoff 60s"; sleep 60; }
done
