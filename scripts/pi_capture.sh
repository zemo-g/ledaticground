#!/bin/bash
# Run on the Pi Zero 2 W remote node. Records one NOAA/METEOR APT pass and ships the
# recording to the Mini over Tailscale for decoding.  (Pi/Linux has GNU `timeout`.)
#   usage: pi_capture.sh [freq_hz] [duration_sec] [label]
set -u
FREQ=${1:-137620000}        # NOAA 15 = 137.620M, NOAA 19 = 137.100M, NOAA 18 = 137.9125M
DUR=${2:-900}               # seconds (~15 min pass)
LABEL=${3:-remote_site}
MINI=${MINI_TS_IP:-MINI_HOST}        # Mini Tailscale IP
MINI_USER=${MINI_USER:-ledaticempire}
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=/tmp/cap_${LABEL}_${TS}.s16
echo "recording $FREQ for ${DUR}s -> $OUT"
# rtl_fm: wide gain for a weak satellite; s16 mono @ 11025 Hz (matches apt.rail)
timeout "$DUR" rtl_fm -f "$FREQ" -M fm -s 48000 -r 11025 -A fast -g 49 -E deemp - \
  2>/tmp/pi_cap.log > "$OUT"
bytes=$(wc -c <"$OUT"); echo "recorded $bytes bytes (~$((bytes/22050))s)"
echo "shipping to $MINI_USER@$MINI ..."
ssh "$MINI_USER@$MINI" 'mkdir -p /tmp/incoming' 2>/dev/null
scp "$OUT" "$MINI_USER@$MINI:/tmp/incoming/" && echo "delivered: /tmp/incoming/$(basename "$OUT")"
