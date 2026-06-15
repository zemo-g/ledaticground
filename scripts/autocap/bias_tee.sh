#!/bin/bash
# BIASTEE-1: safe gated bias-tee toggle for the RTL-SDR (rtl_biast -b 1/0).
#
#   bias_tee.sh on      -- energize +4.5V DC up the coax, ONLY if an LNA is declared present
#   bias_tee.sh off     -- de-energize (always safe; run anytime)
#   bias_tee.sh status  -- report the declared interlock state
#
# THE DANGER: energizing DC into an antenna with NO LNA (or a DC-shorted feed) can damage
# the SDR front-end or the feed. So 'on' is FAIL-CLOSED, gated on TWO conditions:
#   (1) the presence file says exactly 'yes', AND
#   (2) a model file names a known DC-pass LNA (e.g. Sawbird+).
# If either gate fails, it prints REFUSED and exits 1 WITHOUT invoking rtl_biast.
#
# Ships OFF: the presence file (data/lna_present.txt) is ABSENT by default, so the system
# is fail-safe until someone physically installs the LNA and writes lna_present.txt=yes.
#
# CONFIG (file-based, no env vars -- same discipline as the Rail fleet):
#   data/lna_present.txt   content 'yes'  to arm (repo-relative; default ABSENT)
#   ~/.iq/lna_present      content 'yes'  (Pi-side override)
#   ~/.iq/lna_model        names the LNA  (e.g. 'Sawbird+ 137')   (Pi-side)
#   data/lna_model.txt     names the LNA  (repo-relative fallback)
#
# rtl_biast GPIO caveat: 'rtl_biast -b 1' is STICKY across rtl_sdr invocations on some
# dongles -- the explicit '-b 0' in the capture trap is the de-energize guarantee.
#
# DRY_RUN=1 -> log the intended rtl_biast call without invoking hardware.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GD="$(cd "$HERE/../.." && pwd)"
DRY="${DRY_RUN:-0}"
RTL_BIAST="${RTL_BIAST:-rtl_biast}"      # overridable to a stub for testing

log(){ echo "$(date -u +%FT%TZ) [bias_tee] $*"; }

read_first(){ [ -f "$1" ] && head -n1 "$1" | tr -d ' \r\n' || echo ""; }

# presence: Pi-side ~/.iq/lna_present wins, else repo data/lna_present.txt
present=""
if [ -f "$HOME/.iq/lna_present" ]; then present="$(read_first "$HOME/.iq/lna_present")"
elif [ -f "$GD/data/lna_present.txt" ]; then present="$(read_first "$GD/data/lna_present.txt")"; fi
# model: Pi-side ~/.iq/lna_model wins, else repo data/lna_model.txt
model=""
if [ -f "$HOME/.iq/lna_model" ]; then model="$(read_first "$HOME/.iq/lna_model")"
elif [ -f "$GD/data/lna_model.txt" ]; then model="$(read_first "$GD/data/lna_model.txt")"; fi

cmd="${1:-status}"
case "$cmd" in
  on)
    # Fail-CLOSED two-condition gate. Implemented as NESTED ifs (Rail-style discipline
    # carried to bash: don't rely on short-circuit; the point is fail-closed by structure).
    if [ "$present" = "yes" ]; then
      if [ -n "$model" ]; then
        if [ "$DRY" = 1 ]; then
          log "DRY: would run $RTL_BIAST -b 1 (LNA '$model' declared present)"
          exit 0
        fi
        log "energizing bias-tee: $RTL_BIAST -b 1 (LNA '$model')"
        "$RTL_BIAST" -b 1
        exit $?
      else
        echo "REFUSED: LNA declared present but no model named -- never energize an unidentified feed" >&2
        exit 1
      fi
    else
      echo "REFUSED: no LNA declared present -- never energize an empty/DC-shorted feed" >&2
      exit 1
    fi
    ;;
  off)
    if [ "$DRY" = 1 ]; then log "DRY: would run $RTL_BIAST -b 0"; exit 0; fi
    log "de-energizing bias-tee: $RTL_BIAST -b 0"
    "$RTL_BIAST" -b 0
    exit $?
    ;;
  status)
    echo "bias_tee status: present='${present:-<absent>}' model='${model:-<absent>}' (on requires present=yes AND model set; ships OFF)"
    exit 0
    ;;
  *)
    echo "usage: bias_tee.sh on|off|status" >&2
    exit 2
    ;;
esac
