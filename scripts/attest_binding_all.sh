#!/bin/bash
# attest_binding_all.sh -- sweep the raw-IQ dir and emit a PHYSICS_BINDING_RECEIPT for each good
# METEOR LRPT pass that (a) is signal-present (not a noise/spur false-sync), (b) has a FRESH TLE
# (within 3 days of the pass -- SGP4 is only accurate near epoch), and (c) is not already bound.
#
# Mirrors attest_lrpt_decode_all.sh: refresh.sh calls it once per cycle, guarded, OFF the raw
# capture/decode path (it only reads <bin> + <bin>.satdump products + the TLE file, and appends to
# the physics_binding ledger family). A failure here can NEVER break the pull/decode/AIS pipeline.
#
# Idempotent via a per-bin "<bin>.bound" sentinel (survives rsync; the binding ledger stays the
# source of truth, the sentinel just prevents re-work). HONEST: a noise pass or a stale-TLE pass is
# marked processed and SKIPPED (no receipt) -- we never bind a false-sync or against an inaccurate
# orbit. A pass with signal-but-no-clean-Doppler (extraction finds <5 high-SNR windows) is likewise
# marked + skipped. Geo is the node's APPROXIMATE location (precise PENDING GPS-PPS).
#
# Usage: bash scripts/attest_binding_all.sh [raw_iq_dir]   (default ~/.ledatic/roofv2/raw_iq)
# bash-3.2 / macOS safe. set -u, no set -e (one bad pass must not abort the sweep).
set -u

REPO="/Users/ledaticempire/projects/ledaticground"
PY="/opt/homebrew/bin/python3.11"
DRIVER="$REPO/scripts/attest_binding_rollup.sh"
TLEFILE="$REPO/data/tle_weather.txt"
RAWDIR="${1:-$HOME/.ledatic/roofv2/raw_iq}"
LAT="42.0"; LON="-83.1"; ALT_KM="0.19"          # node approximate location (precise geo PENDING GPS-PPS)
RMS_TOL="600"                                    # real-IQ SNR-limited gate (a wrong orbit ~1000+ still fails)

[ -x "$PY" ]      || { echo "BINDALL_ERR: python missing: $PY" >&2; exit 2; }
[ -f "$DRIVER" ]  || { echo "BINDALL_ERR: binding driver missing: $DRIVER" >&2; exit 2; }
[ -f "$TLEFILE" ] || { echo "BINDALL: no TLE file ($TLEFILE) -- nothing to bind (exit 0)"; exit 0; }
[ -d "$RAWDIR" ]  || { echo "BINDALL: no raw_iq dir -- nothing to bind (exit 0)"; exit 0; }

bound=0; skipped=0; nobind=0
for BIN in "$RAWDIR"/iq_*_LRPT_*.bin; do
    [ -f "$BIN" ] || continue
    [ -f "$BIN.bound" ] && continue                              # idempotent: already processed
    SATDIR="${BIN%.bin}.satdump"; MARKER="${BIN%.bin}.decoded"
    [ -d "$SATDIR" ] || continue                                 # decode hasn't run yet -- come back later
    # noise/spur verdict (the same cadu_ok veto): never bind a satdump false-sync on noise.
    if [ -f "$MARKER" ] && grep -qE "FLAT NOISE|\| noise \|" "$MARKER" 2>/dev/null; then
        touch "$BIN.bound"; skipped=$((skipped + 1)); continue
    fi
    # resolve sat -> TLE + epoch-freshness + t0 + fc. Emits "OK <t0_unix> <fc> <orbit> <sat>" and
    # stages l1/l2 to /tmp, or "SKIP <reason>".
    RES="$("$PY" - "$BIN" "$SATDIR/dataset.json" "$TLEFILE" <<'PYEOF'
import sys, json, os, re
from datetime import datetime, timezone, timedelta
binp, dsp, tlef = sys.argv[1:4]
try:
    sat = json.load(open(dsp)).get("satellite", "")
except Exception:
    print("SKIP no-dataset"); sys.exit(0)
if not sat.upper().startswith("METEOR"):
    print("SKIP not-meteor-lrpt"); sys.exit(0)        # LRPT physics-binding = METEOR for now
fc = 137900000                                         # METEOR-M2-3/M2-4 LRPT downlink
m = re.search(r'(\d{8})T(\d{4})Z', os.path.basename(binp))
if not m:
    print("SKIP no-time-token"); sys.exit(0)
d, hm = m.group(1), m.group(2)
t0 = datetime(int(d[:4]), int(d[4:6]), int(d[6:8]), int(hm[:2]), int(hm[2:4]), 0, tzinfo=timezone.utc)
def norm(s): return re.sub(r'[^A-Z0-9]', '', s.upper())   # METEOR-M2-4 / "METEOR-M2 4" -> METEORM24
want = norm(sat)
lines = [l.rstrip("\n") for l in open(tlef)]
l1 = l2 = None
for i in range(len(lines) - 2):
    if norm(lines[i]) == want and lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
        l1, l2 = lines[i + 1], lines[i + 2]; break
if not l2:
    print("SKIP no-tle-for-" + want); sys.exit(0)
ep = l1[18:32].strip()                                 # l1 cols 19-32: YYDDD.DDDDDDDD
ep_dt = datetime(2000 + int(ep[:2]), 1, 1, tzinfo=timezone.utc) + timedelta(days=float(ep[2:]) - 1)
if abs((t0 - ep_dt).total_seconds()) > 3 * 86400:
    print("SKIP tle-stale"); sys.exit(0)               # SGP4 only accurate near epoch
open("/tmp/bindall_l1.txt", "w").write(l1 + "\n")
open("/tmp/bindall_l2.txt", "w").write(l2 + "\n")
print("OK %d %d %s@%s %s" % (int(t0.timestamp()), fc, l1[2:7].strip(), ep, sat))
PYEOF
)"
    set -- $RES
    if [ "${1:-}" != "OK" ]; then
        touch "$BIN.bound"; skipped=$((skipped + 1)); echo "BINDALL: skip $(basename "$BIN") -- $RES"; continue
    fi
    T0U="$2"; FC="$3"; ORBIT="$4"; SAT="$5"
    echo "BINDALL: binding $(basename "$BIN")  sat=$SAT  t0=$T0U  fc=$FC"
    if bash "$DRIVER" --iq "$BIN" --t0-unix "$T0U" --fs 250000 --fc-hz "$FC" \
            --tle-l1 "$(cat /tmp/bindall_l1.txt)" --tle-l2 "$(cat /tmp/bindall_l2.txt)" \
            --sat "$SAT" --orbit "$ORBIT" --lat "$LAT" --lon "$LON" --alt-km "$ALT_KM" \
            --rms-tol-hz "$RMS_TOL" >/dev/null 2>&1; then
        touch "$BIN.bound"; bound=$((bound + 1)); echo "BINDALL: bound $(basename "$BIN")"
    else
        # signal present but no clean Doppler (extraction <5 hi-SNR windows) or driver error: mark
        # processed so we don't retry a no-clean-signal pass every cycle (no receipt was written).
        touch "$BIN.bound"; nobind=$((nobind + 1)); echo "BINDALL: $(basename "$BIN") no clean bind -- marked, no receipt"
    fi
done
echo "BINDALL: done -- bound=$bound skipped=$skipped no-bind=$nobind (dir=$RAWDIR)"
exit 0
