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
  loc="/tmp/incoming_$(basename "$rf")"; scp "${PI_USER}@${PI}:$rf" "$loc"
  echo "decoding $(basename "$rf")"; bash "$GD/scripts/recv_decode.sh" "$loc" roof_node "$LAT" "$LON"
done
