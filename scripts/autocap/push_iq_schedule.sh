#!/bin/bash
# ledaticground / autocap — MINI-SIDE schedule builder + pusher.
#
# THE DECOUPLING KEY. Run periodically (daily LaunchAgent below). It:
#   1. refreshes the TLE set if it's >12h stale (so SGP4 timing doesn't drift),
#   2. enumerates the next 48h of high-elevation passes (enum_passes.py),
#   3. writes them ATOMICALLY to data/iq_schedule.tsv (temp file + mv),
#   4. pushes that file to the Pi at ~/.iq/schedule.tsv IF the Pi is reachable.
#
# CRITICAL DESIGN POINT: pushing is BEST-EFFORT and NON-FATAL. If the roof WiFi is
# down (the exact failure that loses passes today), we still keep a fresh LOCAL
# schedule and exit 0; the next run retries the push. Meanwhile the Pi capture
# agent runs off the LAST schedule it received — so a single missed push costs
# nothing as long as the previous push covered the gap (48h horizon, daily cadence
# => ~2x redundancy). This is what decouples capture from transfer.
#
# This script NEVER touches the radio, AIS/roofmon, or any capture — it only
# computes + ships a plan. It is safe to run anytime.
#
#   push_iq_schedule.sh            # build + (try to) push once
#
# Reuse / consistency notes:
#   * TLE staleness check copies pass_scheduler.sh: BSD `stat -f %m` (Mini=macOS),
#     12h threshold, fetch_tle.sh is set -e + temp->cat so a failed curl leaves the
#     existing TLE intact.
#   * skyfield enumerator runs under /opt/homebrew/bin/python3.11 (the only python
#     with skyfield), same as next_pass.py / pass_schedule.py.
#   * Pi host/user default to the live roofv2 values, overridable by env to match
#     pass_scheduler.sh (PI_HOST / PI_USER).
set -u

GD=/Users/ledaticempire/projects/ledaticground
PY=/opt/homebrew/bin/python3.11
PI_HOST=${PI_HOST:-100.115.30.12}                 # roofv2 (Tailscale) — same as pass_scheduler.sh
PI_USER=${PI_USER:-ledatic}
PI="${PI_USER}@${PI_HOST}"
REMOTE_DIR='~/.iq'                                # Pi consumes ~/.iq/schedule.tsv
REMOTE_TMP='~/.iq/.schedule.tsv.tmp'              # atomic landing on the Pi side too
HOURS=${HOURS:-48}                                # schedule horizon (>=24h required; 48h = redundancy)
MINEL=${MINEL:-40}                                # high-elevation passes only (matches live iq plist)

log(){ echo "$(date -u +%FT%TZ) $*"; }

# ---- 1. refresh TLE if older than 12h (BSD stat; macOS) -------------------------
TLEF="$GD/data/tle_weather.txt"
# stat -f %m -> mtime epoch; missing file => 0 => treated as ancient => refresh.
TAGE=$(( $(date +%s) - $(stat -f %m "$TLEF" 2>/dev/null || echo 0) ))
if [ "$TAGE" -gt 43200 ]; then
  if bash "$GD/scripts/fetch_tle.sh" >/tmp/iqsched_tle.log 2>&1; then
    log "TLE refreshed: $(tail -1 /tmp/iqsched_tle.log)"
  else
    log "TLE refresh failed — keeping existing (age $((TAGE/3600))h)"
  fi
else
  log "TLE fresh (age $((TAGE/3600))h) — no refresh"
fi

# ---- 2. enumerate next ${HOURS}h of passes ------------------------------------
OUT="$GD/data/iq_schedule.tsv"
TMP="${OUT}.tmp.$$"
# enum_passes.py prints the TAB schedule to stdout and exits 0 even on error
# (empty output on failure). Capture to a temp file so we can validate before swap.
if ! "$PY" "$GD/scripts/autocap/enum_passes.py" --hours "$HOURS" --minel "$MINEL" >"$TMP" 2>/tmp/iqsched_enum.log; then
  # exit 0 is the contract; a non-zero here means the interpreter itself failed.
  log "enum_passes.py crashed (see /tmp/iqsched_enum.log) — keeping previous schedule"
  rm -f "$TMP"
  exit 0
fi

NLINES=$(grep -c . "$TMP" 2>/dev/null || echo 0)
if [ "$NLINES" -eq 0 ]; then
  # No passes found (or enumerator soft-failed). Do NOT clobber a good schedule with
  # an empty one — the Pi is better off running the last known-good forward window.
  log "enumeration produced 0 passes — NOT overwriting $(basename "$OUT") (keeping previous)"
  rm -f "$TMP"
  # still attempt to push whatever we already have, in case a prior run never reached the Pi
else
  # ---- 3. atomic local write (temp + mv) ----------------------------------------
  mv -f "$TMP" "$OUT"
  log "wrote $(basename "$OUT"): $NLINES passes (next ${HOURS}h, maxel>=${MINEL})"
fi

# Nothing to push if we have never built a schedule.
if [ ! -s "$OUT" ]; then
  log "no local schedule yet ($OUT empty/absent) — nothing to push"
  exit 0
fi

# ---- 4. best-effort push to the Pi (inline ssh; NO \$SSH variable per spec) -----
# Reachability probe first so a down link is a clean exit-0, not an scp error spew.
if ssh -o ConnectTimeout=8 -o BatchMode=yes "$PI" 'mkdir -p ~/.iq' 2>/dev/null; then
  # scp to a temp name on the Pi, then mv into place over ssh => atomic on the Pi
  # too (the capture agent never reads a half-written schedule). -C compresses the
  # tiny TSV over the (possibly weak) link.
  if scp -C -o ConnectTimeout=8 -o BatchMode=yes "$OUT" "${PI}:${REMOTE_TMP}" 2>/dev/null \
     && ssh -o ConnectTimeout=8 -o BatchMode=yes "$PI" "mv -f ${REMOTE_TMP} ${REMOTE_DIR}/schedule.tsv" 2>/dev/null; then
    log "pushed schedule -> ${PI}:${REMOTE_DIR}/schedule.tsv (${NLINES:-?} passes)"
  else
    log "push FAILED mid-transfer (Pi reachable but scp/mv errored) — local copy kept, will retry next run"
  fi
else
  # The expected-and-tolerated case: roof WiFi down. Local schedule is fresh; the Pi
  # keeps capturing off its last received file. Exit 0 — next run retries.
  log "Pi unreachable ($PI) — kept local $(basename "$OUT"); Pi runs off last pushed schedule (retry next run)"
fi

exit 0
