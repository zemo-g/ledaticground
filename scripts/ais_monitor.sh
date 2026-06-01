#!/bin/bash
# ledaticground continuous AIS monitor (the "flip" — replaces the 137 noise-capture loop).
# DECODE-ON-PI architecture: the roof Pi captures ch-A (161.975 MHz) AND decodes it locally
# (pi_ais_decode.py, pure-Python ~4s on a Zero 2 W), so only tiny JSON crosses the weak roof
# WiFi (~13 KB/s) — never megabytes of samples. The Mini stamps each decode with the capture
# time and appends to the vessel timeline (vessel_log.jsonl). Per cycle we also log the Pi's
# uptime + `vcgencmd get_throttled` (voltage word) to watch the power bank sag / clock endurance.
set -u
GD=/Users/ledaticempire/projects/ledaticground
PI=${PI_USER:-ledatic}@${PI_HOST:-ledaticground-node}
PY=/opt/homebrew/bin/python3.11
SECS=${AIS_SECS:-25}            # capture length per cycle
CYCLE=${AIS_CYCLE:-300}         # seconds between cycles (decode-on-Pi has no big pull, so tighter)
CHAR_EVERY=${CHAR_EVERY:-6}     # run the RFML characterizer every Nth cycle (the node says what it hears)
CHAR_SECS=${CHAR_SECS:-3}       # short capture — pure-python FFT on the Zero is CPU-heavy
MON=/tmp/ais_monitor.log
LOG="$GD/data/vessel_log.jsonl"
CHARLOG="$GD/data/characterize_log.jsonl"
echo "$(date -u +%FT%TZ)  ais_monitor START (decode-on-Pi, dual-channel, exhaustive) — ${SECS}s every ${CYCLE}s" >> "$MON"

CYC=0
while true; do
  ts=$(date +%s)
  # alternate AIS channels each cycle: ch-A 161.975 (even) / ch-B 162.025 (odd).
  # vessels split their reports A/B; covering both is needed once they're in range.
  if [ $((CYC % 2)) -eq 0 ]; then FREQ=161975000; CH=A; else FREQ=162025000; CH=B; fi
  CYC=$((CYC + 1))
  # capture on the Pi, decode on the Pi (exhaustive), return JSON + a META line
  # HARD overall timeout (perl alarm) + ServerAlive: a connect-only timeout let a hung
  # remote command stall the whole loop for an hour after a WiFi blip (2026-06-01). Now the
  # cycle self-aborts at 90s and the Pi-side decode self-kills at 30s.
  out=$(perl -e 'alarm 90; exec @ARGV' ssh -o ConnectTimeout=12 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 "$PI" \
    "timeout $((SECS+3)) rtl_fm -f $FREQ -M fm -s 48000 -g 40 -l 0 /tmp/ais_mon.s16 2>/dev/null; \
     timeout 30 python3 /home/ledatic/pi_ais_decode.py /tmp/ais_mon.s16 2>/dev/null; \
     printf 'META up=%ss temp=%s thr=%s sz=%s' \"\$(cut -d' ' -f1 /proc/uptime)\" \"\$(vcgencmd measure_temp 2>/dev/null|cut -d= -f2||echo na)\" \"\$(vcgencmd get_throttled 2>/dev/null||echo na)\" \"\$(stat -c%s /tmp/ais_mon.s16 2>/dev/null||echo 0)\"" 2>/dev/null)
  if [ -z "$out" ]; then
    echo "$(date -u +%FT%TZ)  PI UNREACHABLE (battery dead / off-net) — endurance ends here" >> "$MON"
    sleep 60; continue
  fi
  meta=$(printf '%s\n' "$out" | grep '^META' | sed 's/^META //')
  n=$(printf '%s\n' "$out" | grep -c '^{')
  # stamp each decoded message with the capture time, append to the timeline
  printf '%s\n' "$out" | grep '^{' | "$PY" -c \
    "import sys,json; ts=int(sys.argv[1]); [print(json.dumps({**json.loads(l),'ts':ts})) for l in sys.stdin]" \
    "$ts" >> "$LOG"
  echo "$(date -u +%FT%TZ)  ch${CH} ${meta:-no-meta} | decoded $n sources" >> "$MON"
  # periodic RFML characterize: the Rail-trained model (pure-python on the Pi) reports WHAT the
  # node is hearing (class + params), not just decoded AIS — short capture, occasional, so the
  # CPU-heavy pure-python FFT doesn't tax the power bank. Ships compact JSON like the decode path.
  if [ $((CYC % CHAR_EVERY)) -eq 0 ]; then
    cout=$(perl -e 'alarm 150; exec @ARGV' ssh -o ConnectTimeout=12 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 "$PI" \
      "timeout $((CHAR_SECS+3)) rtl_fm -f $FREQ -M fm -s 48000 -g 40 -l 0 /tmp/char.s16 2>/dev/null; \
       timeout 120 python3 /home/ledatic/pi_characterize.py /tmp/char.s16 /home/ledatic/audio_softmax.txt /home/ledatic/audio_novelty.txt 2>/dev/null" 2>/dev/null | grep '^{')
    if [ -n "$cout" ]; then
      printf '%s\n' "$cout" | "$PY" -c \
        "import sys,json; ts=int(sys.argv[1]); ch=sys.argv[2]; [print(json.dumps({**json.loads(l),'ts':ts,'ch':ch})) for l in sys.stdin]" \
        "$ts" "$CH" >> "$CHARLOG"
      echo "$(date -u +%FT%TZ)  ch${CH} CHARACTERIZE $cout" >> "$MON"
    fi
  fi
  sleep "$CYCLE"
done
