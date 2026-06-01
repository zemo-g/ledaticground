#!/bin/bash
# On the Mini: decode a remote-node APT recording with our validated pipeline and
# attest under the remote station's identity (file-driven station coords).
#   usage: recv_decode.sh <recording.s16> <station_name> <lat> <lon> [skip_sec] [len_sec]
set -u
GD=/Users/ledaticempire/projects/ledaticground
F=${1:?need recording path}; STA=${2:-remote_site}; LAT=${3:-0.0}; LON=${4:-0.0}
SKIP=${5:-410}; LEN=${6:-110}
[ -f "$F" ] || { echo "no recording at $F"; exit 1; }
cp "$F" /tmp/noaa15_apt.s16
printf '%s\n' "$STA" > "$GD/data/station_name.txt"
printf '%s\n' "$LAT" > "$GD/data/station_lat.txt"
printf '%s\n' "$LON" > "$GD/data/station_lon.txt"
bash "$GD/scripts/decode_real_pass.sh" "$SKIP" "$LEN"
# restore local-station defaults so subsequent local runs aren't mislabeled
printf 'regional_MI\n' > "$GD/data/station_name.txt"
printf '0.0\n' > "$GD/data/station_lat.txt"
printf -- '-0.0\n' > "$GD/data/station_lon.txt"
