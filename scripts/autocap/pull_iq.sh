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

PI_CAPDIR=${PI_CAPDIR:-/home/ledatic/.iq/captures}   # Pi capture store (matches pi autocap)
RAWDIR=${RAWDIR:-/Users/ledaticempire/.ledatic/roofv2/raw_iq}   # Mini landing dir (same as today)
MANIFEST="$RAWDIR/pull_manifest.tsv"                 # name <TAB> bytes <TAB> verdict <TAB> decoded_epoch
MIN_AGE_S=${MIN_AGE_S:-60}                           # skip captures younger than this (still being written)
LOCK="/tmp/iqpull.lock"

log(){ echo "$(date -u +%FT%TZ) $*"; }

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

  # ---- decode (off-radio, idempotent) ----
  # iq_apt_decode.py <bin> <out_prefix>  (prefix = path without .bin, matching capture-side)
  prefix="${bin%.bin}"
  log "DECODE $name (${sz2}B) -> waterfall + image"
  dout=$("$PY" "$DECODER" "$bin" "$prefix" 2>&1)
  drc=$?
  [ "$drc" -eq 0 ] || log "iq_apt_decode returned $drc for $name"
  # one-line verdict for the manifest: prefer the DECODE SYNC line, else the WATERFALL line.
  verdict=$(echo "$dout" | grep -E "DECODE SYNC_LOCK|WATERFALL" | tr '\n' '|' | sed 's/|$//')
  [ -n "$verdict" ] || verdict="(decoder produced no verdict line; rc=$drc)"

  # ---- independent external cross-check: satdump on the SAME bytes (non-fatal, mode auto) ----
  # validate_external.sh exits 1 if satdump is absent; that is intentionally NON-fatal here
  # (matches pass_scheduler.sh's "|| true"). We still record whether it ran.
  vout=$(bash "$VALIDATOR" "$bin" 2>&1)
  vrc=$?
  vtag=$(echo "$vout" | grep -E "^exit=" | head -1)
  [ -n "$vtag" ] || vtag="satdump_rc=$vrc"

  # ---- write the marker LAST (so a crash mid-decode re-tries next tick, not skips) ----
  {
    echo "decoded_epoch=$(date +%s)"
    echo "decoded_utc=$(date -u +%FT%TZ)"
    echo "bytes=$sz2"
    echo "decode_rc=$drc"
    echo "verdict=$verdict"
    echo "satdump=$vtag"
  } > "$marker"

  # ---- append to the Mini manifest (name, bytes, verdict, epoch) ----
  printf '%s\t%s\t%s\t%s\n' "$name" "$sz2" "$verdict | $vtag" "$(date +%s)" >> "$MANIFEST"
  log "done $name -> $verdict | $vtag"
  decoded=$(( decoded + 1 ))
done

log "sweep complete: decoded=$decoded done(skip)=$skipped_done young(defer)=$skipped_young unstable(defer)=$skipped_unstable"
exit 0
