#!/bin/bash
# ledaticground — MINI-SIDE PULLER + DECODER (the decoupled-transfer half).
#
# DESIGN (Reilly 2026-06-09): satellite CAPTURE on the Pi is now independent of WiFi.
# The Pi captures into its own 51G /home store on its own schedule (pi-side autocap),
# so a flaky roof link at AOS can no longer cost a pass. This script is the LAZY
# back-haul: whenever the Pi is reachable it rsyncs COMPLETED captures down to the Mini
# and decodes each NEW one exactly once. If the link is down it simply does nothing and
# the next 10-min tick (or the next Mini reboot) catches up — captures wait safely on
# the Pi until then.
#
#   pull_iq.sh            # one sweep: pull completed captures, decode new ones, exit
#
# It NEVER touches the radio, NEVER stops/starts roofmon, NEVER deletes on the Pi
# (rsync without --delete; the Pi owns its own 7-day retention). Pure read-side.
#
# Guards the partial-write race two ways so we never decode a half-written capture:
#   1) rsync --partial keeps interrupted transfers resumable (never a truncated final).
#   2) MIN_AGE gate: a .bin whose mtime is within MIN_AGE_S of now is assumed still
#      being written by the Pi's rtl_sdr and is SKIPPED this sweep (next tick gets it).
#   3) belt-and-suspenders: size-stability check (two stats ~3s apart) before decode.
#
# macOS / bash 3.2 only: no mapfile, no associative arrays, no ${x,,}. BSD stat -f.
set -u

GD=/Users/ledaticempire/projects/ledaticground
PI_HOST=${PI_HOST:-100.115.30.12}; PI_USER=${PI_USER:-ledatic}    # roofv2 Pi Zero 2 W
PI="${PI_USER}@${PI_HOST}"
SSH="ssh -o ConnectTimeout=10 -o BatchMode=yes"
PY=/opt/homebrew/bin/python3.11
DECODER="$GD/scripts/iq_apt_decode.py"
VALIDATOR="$GD/scripts/validate_external.sh"

# ---- ORBCOMM front-end (MODE=ORBCOMM captures route here instead of iq_apt_decode) ----
# An Orbcomm capture is a raw cu8 IQ recording centred on the Orbcomm channel (NOT an
# FM-audio APT/LRPT file), so the APT image decoder is meaningless for it. Its OFF-RADIO
# characterization path is: cu8 -> 38.4k int8 IQ via iq_to_orbcomm_char.py -> the FIXED
# input /tmp/orbcomm_char_in.s8 -> src/orbcomm_char.rail (proof-of-RECEPTION: carrier_present
# / offset / SNR / sym_rate, PAYLOAD=NONE proprietary). railrun.sh flock-serializes the
# shared /tmp/rail_out. This routing matches scripts/orbcomm_monitor.sh's characterize_capture.
RAILRUN="$GD/scripts/railrun.sh"
ORBCOMM_FRONTEND="$GD/scripts/iq_to_orbcomm_char.py"
ORBCOMM_CHAR_RAIL="$GD/src/orbcomm_char.rail"
ORBCOMM_FS=${ORBCOMM_FS:-250000}              # raw cu8 capture sample rate (program-wide)
# Orbcomm per-sat downlink to extract. The capture is centred ON the channel, so
# center-hz == chan-hz and the front-end's residual-offset measurement is honest
# (LO error + Doppler only). Exact per-sat value is an open_question; the front-end
# default (137662500) is a representative in-band channel. Override via ORBCOMM_CHAN_HZ.
ORBCOMM_CHAN_HZ=${ORBCOMM_CHAN_HZ:-137662500}
ORBCOMM_RAIL_TIMEOUT=${ORBCOMM_RAIL_TIMEOUT:-60}

PI_CAPDIR=${PI_CAPDIR:-/home/ledatic/.iq/captures}   # Pi capture store (matches pi autocap)
RAWDIR=${RAWDIR:-/Users/ledaticempire/.ledatic/roofv2/raw_iq}   # Mini landing dir (same as today)
MANIFEST="$RAWDIR/pull_manifest.tsv"                 # name <TAB> bytes <TAB> verdict <TAB> decoded_epoch
MIN_AGE_S=${MIN_AGE_S:-60}                           # skip captures younger than this (still being written)
LOCK="/tmp/iqpull.lock"

log(){ echo "$(date -u +%FT%TZ) $*"; }

# Extract the MODE field from a capture basename. Convention (pass_scheduler.sh
# capture_iq_pass line 68, orbcomm_monitor.sh line 71): iq_<sat>_el<E>_<MODE>_<ts>.bin —
# MODE is the underscore-delimited field immediately before the trailing <ts>.bin. The
# <sat> portion has its spaces stripped (e.g. NOAA15 -> NOAA15) so it contributes no
# extra underscores; <el<E>> is a single field; <ts> is a single 20060102T1504Z field.
# So MODE = the 2nd-from-last underscore field. Unparseable / legacy names -> "APT"
# (the historical default, keeping every existing capture on the unchanged image path).
# bash 3.2: no ${x,,} / arrays-from-string; drive it with parameter expansion + cut.
mode_of(){
  local n="$1" core ts_stripped
  case "$n" in
    iq_*) ;;                        # only iq_*.bin carry the convention
    *) echo "APT"; return ;;
  esac
  # strip trailing ".bin", then the trailing "_<ts>" -> the field now ending the string is MODE
  core="${n%.bin}"
  ts_stripped="${core%_*}"          # drop _<ts>
  case "$ts_stripped" in
    *_*) echo "${ts_stripped##*_}" ;;   # MODE = last field of what remains
    *) echo "APT" ;;                    # too few fields to be the new convention
  esac
}

# Single-flight: 10-min ticks must never overlap (a long backlog decode can outrun a
# tick). mkdir is atomic; stale lock from a crashed run is reaped after 2h.
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -d "$LOCK" ]; then
    age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
    if [ "$age" -gt 7200 ]; then
      log "stale lock (${age}s) — reaping"; rmdir "$LOCK" 2>/dev/null
      mkdir "$LOCK" 2>/dev/null || { log "could not re-acquire lock; exit"; exit 0; }
    else
      log "another pull in progress (lock ${age}s old) — skip this tick"; exit 0
    fi
  else
    log "could not acquire lock; exit"; exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

mkdir -p "$RAWDIR"

# ---------- 0. reachability: if the Pi is down, do nothing (captures wait safely) ----------
if ! $SSH "$PI" "true" 2>/dev/null; then
  log "Pi $PI_HOST unreachable — nothing to pull this tick (captures persist on the Pi)"
  exit 0
fi

# ---------- 1. rsync COMPLETED captures down (NEVER --delete; the Pi keeps its files) ----------
# openrsync (BSD, proto 29) on macOS: use only portable flags. -a preserves mtime so the
# MIN_AGE gate below is meaningful. --partial survives a link drop mid-transfer (the
# leftover is a hidden temp, never the final name). We pull EVERYTHING here but gate the
# DECODE step on age/stability — rsync of an in-flight file just gets re-synced next tick.
# Trailing slash on source = copy contents into RAWDIR (not a nested captures/ dir).
log "pull: rsync $PI:$PI_CAPDIR/ -> $RAWDIR/"
rsync -a --partial --exclude='*.tmp' -e "ssh -o ConnectTimeout=10 -o BatchMode=yes" \
      "$PI:$PI_CAPDIR/" "$RAWDIR/" >/tmp/iqpull_rsync.log 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
  # Non-fatal: partial pulls are fine, we only decode files that pass the stability gate.
  log "rsync exit=$rc (partial/transient ok) — see /tmp/iqpull_rsync.log; proceeding to decode whatever landed"
fi

# ---------- 2. decode each NEW completed .bin exactly once (idempotent via .decoded marker) ----------
now=$(date +%s)
decoded=0; skipped_young=0; skipped_done=0; skipped_unstable=0
# bash 3.2: drive the loop with a glob, guard the no-match case (nullglob is unavailable).
for bin in "$RAWDIR"/iq_*.bin; do
  [ -e "$bin" ] || continue                 # literal-glob guard when no captures exist
  name=$(basename "$bin")
  marker="${bin%.bin}.decoded"

  # idempotent: already decoded -> skip (this is what makes re-runs free)
  if [ -e "$marker" ]; then skipped_done=$(( skipped_done + 1 )); continue; fi

  # partial-write guard #1: too young -> the Pi may still be writing it; defer to next tick
  mtime=$(stat -f %m "$bin" 2>/dev/null || echo "$now")
  age=$(( now - mtime ))
  if [ "$age" -lt "$MIN_AGE_S" ]; then
    log "defer $name (age ${age}s < ${MIN_AGE_S}s; still landing)"; skipped_young=$(( skipped_young + 1 )); continue
  fi

  # partial-write guard #2: size must be stable across two reads (catches an in-progress
  # rsync into this very dir, independent of mtime). 3s settle window.
  sz1=$(stat -f %z "$bin" 2>/dev/null || echo 0)
  sleep 3
  sz2=$(stat -f %z "$bin" 2>/dev/null || echo 0)
  if [ "$sz1" != "$sz2" ] || [ "$sz1" -lt 1000000 ]; then
    log "defer $name (size unstable/too small: ${sz1}->${sz2}B)"; skipped_unstable=$(( skipped_unstable + 1 )); continue
  fi

  # ---- decode (off-radio, idempotent) — MODE-aware routing ----
  # The capture filename encodes the pass MODE (iq_<sat>_el<E>_<MODE>_<ts>.bin). APT/LRPT
  # are FM-audio image passes -> the existing iq_apt_decode + satdump path. ORBCOMM is a raw
  # cu8 IQ recording -> the Orbcomm characterization front-end (image decode is meaningless
  # for it). Every existing MODE (APT, LRPT, anything not ORBCOMM) takes the unchanged path.
  prefix="${bin%.bin}"
  mode=$(mode_of "$name")
  if [ "$mode" = "ORBCOMM" ]; then
    # ===== ORBCOMM branch: route IQ to the proof-of-RECEPTION front-end =====
    # (1) cu8 -> 38.4k int8 IQ at the fixed contract path /tmp/orbcomm_char_in.s8.
    #     Capture is centred ON the channel, so center-hz == chan-hz (residual-offset honest).
    log "DECODE $name (${sz2}B) [MODE=ORBCOMM] -> orbcomm front-end (char, not APT image)"
    fout=$("$PY" "$ORBCOMM_FRONTEND" "$bin" \
             --chan-hz "$ORBCOMM_CHAN_HZ" --center-hz "$ORBCOMM_CHAN_HZ" --fs "$ORBCOMM_FS" 2>&1)
    frc=$?
    if [ "$frc" -ne 0 ]; then
      log "orbcomm front-end channelize returned $frc for $name (see verdict)"
    fi
    # (2) src/orbcomm_char.rail reads /tmp/orbcomm_char_in.s8 -> CHAR measurements (the FACT).
    #     railrun.sh flock-serializes the shared /tmp/rail_out so parallel sweeps are safe.
    dout=$(bash "$RAILRUN" "$ORBCOMM_CHAR_RAIL" "$ORBCOMM_RAIL_TIMEOUT" 2>/dev/null)
    drc=$?
    [ "$drc" -eq 0 ] || log "orbcomm_char.rail returned $drc for $name"
    # one-line CHAR verdict for the manifest (all CHAR lines collapsed to a | string).
    verdict=$(printf '%s\n' "$dout" | grep '^CHAR ' | tr '\n' '|' | sed 's/|$//')
    [ -n "$verdict" ] || verdict="(orbcomm_char produced no CHAR line; frc=$frc rc=$drc)"
    # ORBCOMM has NO satdump reference pipeline (validate_external.sh only knows APT/LRPT);
    # its independent cross-check is the Doppler fingerprint (orbcomm_doppler.sh, separate
    # step). Record the front-end summary line as the cross-check tag instead of satdump.
    vtag=$(printf '%s\n' "$fout" | grep -E '^ORBCOMM_FRONTEND' | head -1)
    [ -n "$vtag" ] || vtag="orbcomm_frontend_rc=$frc"
    xcheck_label="frontend"     # not satdump — Doppler fingerprint is the real cross-check
  else
    # ===== existing APT/LRPT image path (UNCHANGED) =====
    # iq_apt_decode.py <bin> <out_prefix>  (prefix = path without .bin, matching capture-side)
    log "DECODE $name (${sz2}B) -> waterfall + image"
    dout=$("$PY" "$DECODER" "$bin" "$prefix" 2>&1)
    drc=$?
    [ "$drc" -eq 0 ] || log "iq_apt_decode returned $drc for $name"
    # one-line verdict for the manifest: prefer the DECODE line, else the WATERFALL line.
    # (DECODE prints "skipped (LRPT)" for Meteor files — APT sync-lock is meaningless there.)
    verdict=$(echo "$dout" | grep -E "DECODE |WATERFALL" | tr '\n' '|' | sed 's/|$//')
    [ -n "$verdict" ] || verdict="(decoder produced no verdict line; rc=$drc)"

    # ---- independent external cross-check: satdump on the SAME bytes (non-fatal, mode auto) ----
    # validate_external.sh exits 1 if satdump is absent; that is intentionally NON-fatal here
    # (matches pass_scheduler.sh's "|| true"). We still record whether it ran.
    # For LRPT it also emits "CADUS=n cadu_bytes=b" — the AUTHORITATIVE LRPT verdict
    # (deterministic deframed-byte count; the waterfall heuristic is advisory only).
    vout=$(bash "$VALIDATOR" "$bin" 2>&1)
    vrc=$?
    vtag=$(echo "$vout" | grep -E "^(exit=|CADUS=)" | head -2 | tr '\n' ' ' | sed 's/ $//')
    [ -n "$vtag" ] || vtag="satdump_rc=$vrc"
    xcheck_label="satdump"
  fi

  # ---- write the marker LAST (so a crash mid-decode re-tries next tick, not skips) ----
  # The cross-check field key is MODE-aware: image modes record satdump=, ORBCOMM records
  # xcheck= (its real independent cross-check is the Doppler fingerprint, a separate step).
  {
    echo "decoded_epoch=$(date +%s)"
    echo "decoded_utc=$(date -u +%FT%TZ)"
    echo "mode=$mode"
    echo "bytes=$sz2"
    echo "decode_rc=$drc"
    echo "verdict=$verdict"
    echo "${xcheck_label:-satdump}=$vtag"
  } > "$marker"

  # ---- append to the Mini manifest (name, bytes, verdict, epoch) ----
  printf '%s\t%s\t%s\t%s\n' "$name" "$sz2" "$verdict | $vtag" "$(date +%s)" >> "$MANIFEST"
  log "done $name -> $verdict | $vtag"
  decoded=$(( decoded + 1 ))
done

log "sweep complete: decoded=$decoded done(skip)=$skipped_done young(defer)=$skipped_young unstable(defer)=$skipped_unstable"
exit 0
