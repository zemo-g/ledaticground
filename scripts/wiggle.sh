#!/bin/bash
# ledaticground MINI-SIDE antenna wiggle wrapper. The roof-day one-liner:
#
#   wiggle.sh on [gain]   # stop Pi capture services, start detached probe loop
#   wiggle.sh watch       # live-tail the meter (reconnect-safe; Ctrl-C anytime)
#   wiggle.sh off         # kill probe, restart services
#   wiggle.sh status      # probe + services + last reading
#
# The probe itself lives ON the Pi (autocap/wiggle_probe.sh staged to ~/) and
# is nohup-detached, so flaky roof WiFi only interrupts WATCHING, never the
# meter or the pass-guard (probe auto-restores services at next-AOS minus 7min
# even if this Mac never reconnects). History: Pi ~/roof_data/wiggle.{jsonl,log}.
#
# READING IT: maximize 137delta (in-band 136-138 floor minus 138.5-140 ref).
# +1 dB delta from an orientation change is real; the deficit to a decoding
# station is ~8 dB, so expect wiggles to claw 1-3 dB — the LNA is the rest.
set -u
PI=${PI_USER:-ledatic}@${PI_HOST:-100.115.30.12}
PROBE='~/wiggle_probe.sh'
case "${1:-status}" in
  on)     ssh "$PI" "$PROBE start ${2:-40}" ;;
  off)    ssh "$PI" "$PROBE stop" ;;
  status) ssh "$PI" "$PROBE status" ;;
  watch)  echo "(Ctrl-C stops watching only — probe keeps running; 'wiggle.sh off' to end session)"
          ssh "$PI" "tail -n 5 -f ~/roof_data/wiggle.log" ;;
  *) echo "usage: wiggle.sh on [gain] | watch | off | status"; exit 1 ;;
esac
