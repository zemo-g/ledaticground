#!/bin/bash
# RS41-7: SCAN-based capture for Vaisala RS41 radiosondes (400.0-406.0 MHz).
#
# RS41 sondes are balloon-launched with NO orbital TLE, so they CANNOT go through the
# SGP4-orbit-driven autocap scheduler (scripts/autocap/enum_passes.py -- that engine needs
# an EarthSatellite + AOS/LOS geometry; the SATS dict has no 400 MHz entry). Instead this
# is a frequency SCAN: rtl_power sweeps 400-406 MHz to find an active sonde carrier, then
# rtl_sdr grabs a fixed-duration IQ window at the found frequency.
#
# Mirrors pi_iq_capture.sh's discipline: roofmon (AIS) preempt + ALWAYS-resume, atomic
# .tmp -> .bin publish, bias-tee coupled to active-capture-only (BIASTEE-1), TERM/INT trap.
# The capture filename carries the _RS41_ MODE token so the pull path routes it to
# decode_rs41.sh (sibling of iq_apt_decode.py's _LRPT_ dispatch).
#
# *** HARDWARE BLOCKER ***: the current rooftop antenna (the 120-deg halo) is tuned to
# 136-138 MHz ONLY -- it CANNOT hear 400 MHz. So if data/antenna_band.txt is not '400',
# this script prints a LOUD blocker message and EXITS NON-ZERO without capturing, so it
# never produces a misleading empty-spectrum capture (same lesson as the decommissioned-
# NOAA empty-capture trap). Live RS41 reception is gated on a separate 400 MHz antenna +
# LNA (docs/LNA_SPEC.md) -- a procurement blocker. The SOFTWARE chain validates synthetically
# NOW via scripts/selftest.sh (gen_rs41_*.py -> rs41_decode.rail -> rs41_attest.rail).
#
#   usage: rs41_capture.sh [dur_sec]   (default 30)
#   DRY_RUN=1 -> log intended actions without touching the SDR or roofmon.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GD="$(cd "$HERE/.." && pwd)"
IQ="${IQ_DIR:-$HOME/.iq}"
CAPS="$IQ/captures"
GAIN="${GAIN:-49}"
SR=250000
DUR="${1:-30}"
DRY="${DRY_RUN:-0}"
FMIN=400000000
FMAX=406000000
mkdir -p "$CAPS"

log(){ echo "$(date -u +%FT%TZ) [rs41cap] $*"; }
read_first(){ [ -f "$1" ] && head -n1 "$1" | tr -d ' \r\n' || echo ""; }

# ---- ANTENNA-BAND INTERLOCK: refuse if the kit is not a 400 MHz antenna ----
band="$(read_first "$GD/data/antenna_band.txt")"
if [ "$band" != "400" ]; then
  echo "BLOCKER: needs 400 MHz antenna; the rooftop halo is 136-138 MHz only (data/antenna_band.txt='${band:-<unset>}')." >&2
  echo "         RS41 lives at 400.0-406.0 MHz -- a separate antenna + 400-band LNA is a procurement blocker (docs/LNA_SPEC.md)." >&2
  echo "         Refusing to capture an empty 400 MHz spectrum on a 137 MHz antenna (no fabricated/misleading capture)." >&2
  echo "         The RS41 SOFTWARE chain validates SYNTHETICALLY now: bash scripts/selftest.sh (RS41 stanza)." >&2
  exit 2
fi

# bias-tee toggle (fail-closed; no-op/REFUSED if no LNA declared present)
bias_off(){ DRY_RUN="$DRY" bash "$HERE/autocap/bias_tee.sh" off >/dev/null 2>&1 || true; }
bias_on(){  DRY_RUN="$DRY" bash "$HERE/autocap/bias_tee.sh" on  >/dev/null 2>&1 || true; }

resume_roofmon(){
  local i a
  for i in 1 2 3 4 5 6; do
    [ "$DRY" = 1 ] && { log "DRY: would start roofmon"; return 0; }
    sudo systemctl start roofmon.service 2>/dev/null
    a=$(systemctl is-active roofmon.service 2>/dev/null)
    [ "$a" = active ] && { log "roofmon active (AIS resumed)"; return 0; }
    sleep 5
  done
  log "!! WARNING roofmon not confirmed active -- Pi-side deadman should recover it"; return 1
}
trap 'log "signal -> free SDR + bias-tee OFF + restore AIS"; [ "$DRY" = 1 ] || pkill -f "rtl_sdr -f\|rtl_power" 2>/dev/null; bias_off; resume_roofmon; exit 143' TERM INT

ts="$(date -u +%Y%m%dT%H%MZ)"
scan="/tmp/rs41_scan_${ts}.csv"

if [ "$DRY" = 1 ]; then
  log "DRY: stop roofmon; bias on; rtl_power -f ${FMIN}:${FMAX}:5k -1 $scan; pick peak; rtl_sdr -f <peak> -s $SR -g $GAIN <name>; bias off; resume roofmon"
  bias_on; bias_off
  exit 0
fi

sudo systemctl stop roofmon.service || { log "could not stop roofmon; abort"; exit 1; }
sleep 1
bias_on

# 1. SCAN 400-406 MHz for an active carrier (one-shot rtl_power sweep, 5 kHz bins)
log "scanning ${FMIN}-${FMAX} Hz for an active RS41 carrier..."
timeout -k 5 60 rtl_power -f "${FMIN}:${FMAX}:5k" -g "$GAIN" -1 "$scan" >/tmp/rs41_power.log 2>&1
rc=$?
if [ ! -s "$scan" ]; then
  log "scan produced no data (rc=$rc) -- aborting (no capture)"; bias_off; resume_roofmon; exit 1
fi

# 2. pick the strongest bin above the median floor (awk: cols 7.. are dB per bin)
peak=$(awk -F',' '
  { fstart=$3+0; fstep=$5+0;
    for(i=7;i<=NF;i++){ v=$i+0; sum[NR","i]=v; vals[++n]=v;
      bf[n]=fstart+(i-7)*fstep } }
  END{ for(k=1;k<=n;k++){ a[k]=vals[k] }
       # simple max-over-floor pick
       asort_max=-999; idx=0;
       for(k=1;k<=n;k++){ if(vals[k]>asort_max){asort_max=vals[k]; idx=k} }
       if(idx>0) printf("%d", bf[idx]) }' "$scan" 2>/dev/null)
if [ -z "$peak" ]; then
  log "no carrier peak found in scan -- aborting (no capture)"; bias_off; resume_roofmon; exit 1
fi
log "peak carrier ~${peak} Hz"

# 3. grab a fixed-duration IQ window at the peak. _RS41_ MODE token routes the pull path.
name="iq_RS41_scan_f${peak}_${ts}.bin"
out="$CAPS/$name"; tmp="$out.tmp"
rdur="$DUR"
log "capturing ${rdur}s at ${peak} Hz -> $name (g${GAIN})"
timeout -k 5 "$rdur" rtl_sdr -f "$peak" -s "$SR" -g "$GAIN" "$tmp" >/tmp/rs41_rtl.log 2>&1
rc=$?
bias_off
resume_roofmon
sz=$(stat -c%s "$tmp" 2>/dev/null || stat -f%z "$tmp" 2>/dev/null || echo 0)
if [ "${sz:-0}" -lt 1000000 ]; then log "capture too small (${sz}B rc=$rc) -- discarding"; rm -f "$tmp"; exit 1; fi
mv -f "$tmp" "$out"
log "captured $name (${sz}B, rc=$rc). Decode: bash scripts/decode_rs41.sh $out"
