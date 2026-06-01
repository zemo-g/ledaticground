#!/bin/bash
# ledaticground antenna benchmark: capture from the roof Pi + score the antenna.
# The one command to run before/after swapping antennas. 162 is always measurable
# (AIS is always on air); 137 needs a satellite overhead (use the pass pipeline).
#
#   antenna_score.sh 162 [label] [secs]      # default secs=10 (~4.8MB IQ)
#
# Uses rsync --partial so the flaky roof WiFi can resume the pull.
set -u
GD=/Users/ledaticempire/projects/ledaticground
PI=${PI_USER:-ledatic}@${PI_HOST:-ledaticground-node}
PY=/opt/homebrew/bin/python3.11
BAND=${1:?usage: antenna_score.sh <162|137> [label] [secs] [gain]}
LABEL=${2:-"$(date -u +%Y%m%dT%H%MZ)"}
SECS=${3:-10}
GAIN=${4:-40}

if [ "$BAND" = "162" ]; then
  N=$(( 240000 * SECS ))
  echo "capturing ${SECS}s IQ @162.0MHz gain=${GAIN} from $PI ..."
  ssh "$PI" "timeout $((SECS+3)) rtl_sdr -f 162000000 -s 240000 -n $N -g $GAIN /tmp/score.iq 2>/dev/null; ls -l /tmp/score.iq" || { echo "Pi unreachable"; exit 1; }
  dst="$GD/data/score_${LABEL}.iq"
  rsync --partial --timeout=40 -e ssh "$PI:/tmp/score.iq" "$dst" 2>/dev/null || scp -C "$PI:/tmp/score.iq" "$dst" || { echo "pull failed"; exit 1; }
  $PY "$GD/scripts/antenna_score.py" 162 "$GD/data/score_${LABEL}.iq" "$LABEL"
else
  echo "137 scoring needs a satellite pass — the orchestrator logs the 2400Hz ratio per pass."
  echo "To score an APT capture manually:  antenna_score.py 137 <apt_s16> <label>"
fi
