#!/bin/bash
# ledaticground: pull 137 MHz APT/LRPT weather birds from CelesTrak.
# (Rail's native TLS is fragile on these CDN hosts — sanctioned shell shim.)
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)/data"
mkdir -p "$DIR"
B="https://celestrak.org/NORAD/elements/gp.php?FORMAT=tle"
# weather group carries METEOR-M2 series; NOAA 15/19 fetched by catalog number
# (NOAA 18 decommissioned 2025, no longer in catalog).
curl -fsS "${B}&GROUP=weather" -o "$DIR/_w.txt"
curl -fsS "${B}&CATNR=25338"   -o "$DIR/_n15.txt"   # NOAA 15
curl -fsS "${B}&CATNR=33591"   -o "$DIR/_n19.txt"   # NOAA 19
cat "$DIR/_n15.txt" "$DIR/_n19.txt" "$DIR/_w.txt" > "$DIR/tle_weather.txt"
rm -f "$DIR/_w.txt" "$DIR/_n15.txt" "$DIR/_n19.txt"
date -u +%s > "$DIR/now_unix.txt"
echo "TLEs: $(grep -c '^1 ' "$DIR/tle_weather.txt") sats | epoch $(cat "$DIR/now_unix.txt")"
