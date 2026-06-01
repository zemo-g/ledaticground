#!/bin/bash
# Mini brain: predict the next NOAA pass, trigger the Pi to record it, pull the
# recording over Tailscale, decode + attest. Run in a loop (or as a LaunchAgent).
set -u
GD=/Users/ledaticempire/projects/ledaticground
PI=${PI_HOST:-ledaticground-roof}; PI_USER=${PI_USER:-ledatic}
PY=/opt/homebrew/bin/python3.11; MINEL=${MINEL:-30}
LAT=${NODE_LAT:-0.0}; LON=${NODE_LON:--0.0}
while true; do
  info=$($PY "$GD/scripts/next_pass.py" --minel "$MINEL")
  if [[ "$info" == NONE* ]]; then echo "$(date -u +%H:%M) no pass>=${MINEL}deg soon; sleep 1h"; sleep 3600; continue; fi
  eval "$info"   # SAT MINS DUR ELEV FREQ AOS_EPOCH
  echo "$(date -u +%H:%M) next: $SAT in ${MINS}min El${ELEV} @${FREQ}"
  now=$(date +%s); w=$(( AOS_EPOCH - now - 30 )); [ "$w" -gt 0 ] && sleep "$w"
  RDUR=$(( (DUR+3)*60 ))
  echo "trigger Pi record ${RDUR}s"; ssh "${PI_USER}@${PI}" "nohup /usr/local/bin/pi_record.sh $FREQ $RDUR roof >/tmp/recout 2>&1 &" || { echo "Pi unreachable; skip"; sleep 300; continue; }
  sleep $(( RDUR + 60 ))
  rf=$(ssh "${PI_USER}@${PI}" "ls -t /tmp/cap_roof_*.s16 2>/dev/null | head -1")
  [ -n "$rf" ] || { echo "no recording on Pi"; continue; }
  # roof WiFi is weak -> have the Pi extract the high-elevation window (~120s, ~2.6MB)
  # centered on the pass peak (recording starts AOS-30s; peak ~30+DUR*30s in), pull only that
  WSKIP=$(( DUR*30 - 30 )); [ "$WSKIP" -lt 0 ] && WSKIP=0
  ssh "${PI_USER}@${PI}" "dd if='$rf' of=/tmp/roof_win.s16 bs=22050 skip=$WSKIP count=120 2>/dev/null"
  loc="/tmp/incoming_win.s16"; scp -C "${PI_USER}@${PI}:/tmp/roof_win.s16" "$loc"
  echo "decoding windowed $(basename "$rf") ($(wc -c <"$loc" 2>/dev/null) bytes)"
  bash "$GD/scripts/recv_decode.sh" "$loc" roof_node "$LAT" "$LON" 0 110
done
