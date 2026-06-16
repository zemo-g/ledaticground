#!/bin/bash
# attest_ais_rollup.sh -- Mini-side AIS attestation roll-up driver (RECEIPT_CONTRACT v=2, AC-1).
#
# A NEW, SEPARATE, IDEMPOTENT step that sits OFF the raw decode path. It reads the accumulated
# AIS source jsonl, takes the UNSIGNED TAIL since the last signed batch_end, builds the
# deterministic canonical-frame product, fetches the live beacon pulse, stages the previous
# chain_hash for real hash-chaining, then invokes the pure-Rail signer via the flock wrapper.
# The signer appends one v=2 line to data/ais_receipts.jsonl, rewrites the legacy single-object
# data/ais_receipt.json, and writes the tail chain_hash to data/ais_fact_chain.txt.
#
# The raw capture/decode path (ais_monitor.sh, pi_ais_decode.py, pull_iq.sh, pi_iq_capture.sh)
# is NEVER touched by this driver.
#
# ---------------------------------------------------------------------------------------------
# LIVE DEPLOY TARGET (production source row file -- NOT read during this synthetic build):
#   ~/.ledatic/roofv2/ais.jsonl
# The deploy hook is ONE appended line in ~/.ledatic/roofv2/refresh.sh, AFTER its existing
# transit_log.py call:
#   bash /Users/ledaticempire/projects/ledaticground/scripts/attest_ais_rollup.sh >> "$D/attest.log" 2>&1 || true
# In production AIS_SRC defaults to ~/.ledatic/roofv2/ais.jsonl. For OFFLINE / SYNTHETIC
# validation pass AIS_SRC=<fixture> in the environment (or arg 1) so this NEVER reads the
# live node path during a build.
# ---------------------------------------------------------------------------------------------
#
# Usage:
#   AIS_SRC=/abs/fixture.jsonl bash scripts/attest_ais_rollup.sh    # synthetic / offline
#   bash scripts/attest_ais_rollup.sh                               # production (live src default)
#
# bash-3.2 / macOS safe. No set -e (a failing curl must fall through to the honest PENDING
# pulse, not abort the driver). Each failure path is guarded explicitly.
set -u

REPO="/Users/ledaticempire/projects/ledaticground"
PY="/opt/homebrew/bin/python3.11"

# Source row file: arg 1 > $AIS_SRC env > live default. The live default is the DEPLOY TARGET;
# synthetic validation always passes a fixture so the live node path is never read in a build.
AIS_SRC="${1:-${AIS_SRC:-$HOME/.ledatic/roofv2/ais.jsonl}}"

LEDGER="$REPO/data/ais_receipts.jsonl"
CURSOR="$REPO/data/ais_rollup_cursor.txt"   # tracks the last signed batch_end (unix seconds)
FRAMES="/tmp/ais_batch_frames.txt"
PREV_FILE="/tmp/ais_prev_sha.txt"
BSTART_FILE="/tmp/ais_batch_start.txt"
BEND_FILE="/tmp/ais_batch_end.txt"
N_FILE="/tmp/ais_batch_n.txt"

# Python interpreter is required (file-based config; do not assume PATH).
if [ ! -x "$PY" ]; then
    echo "ROLLUP_ERR: python3.11 not executable at $PY" >&2
    exit 2
fi

# Source file must exist; if absent there is simply nothing to attest yet (idempotent no-op).
if [ ! -f "$AIS_SRC" ]; then
    echo "ROLLUP: source $AIS_SRC absent -- nothing to attest (exit 0)"
    exit 0
fi

# --- Determine the cursor (last signed batch_end) -------------------------------------------
# Prefer the persisted cursor file; if missing, derive it from the ledger tail's batch_end so a
# fresh checkout that already has a ledger does not re-sign everything. Empty -> 0 (genesis).
LAST_END="0"
if [ -f "$CURSOR" ]; then
    LAST_END="$(cat "$CURSOR" 2>/dev/null)"
fi
case "$LAST_END" in ''|*[!0-9]*) LAST_END="0" ;; esac
if [ "$LAST_END" = "0" ] && [ -f "$LEDGER" ]; then
    DERIVED="$(tail -1 "$LEDGER" 2>/dev/null | "$PY" -c "
import sys,json,re
line=sys.stdin.read().strip()
if not line:
    print('0'); sys.exit(0)
try:
    o=json.loads(line); r=o.get('receipt','')
    m=re.search(r'batch_end=([0-9]+)', r)
    print(m.group(1) if m else '0')
except Exception:
    print('0')
" 2>/dev/null)"
    case "$DERIVED" in ''|*[!0-9]*) DERIVED="0" ;; esac
    LAST_END="$DERIVED"
fi

# --- Build the canonical batch from rows with ts > LAST_END ----------------------------------
# Canonical per-frame string (one per line): mmsi=..|type=..|lat=..|lon=..|ts=..
# Sorted by (ts, mmsi) for determinism so a verifier with the same source rows recomputes the
# identical product_sha256. lat/lon stay text exactly as the source emitted them (no float math).
# transit_log.py clean_ts discipline: drop rows with a missing/non-int ts or a pre-NTP outlier
# (ts < 1_000_000_000, i.e. before ~2001) -- those are unscrubbed clock garbage, not provenance.
SUMMARY="$("$PY" - "$AIS_SRC" "$LAST_END" "$FRAMES" "$BSTART_FILE" "$BEND_FILE" "$N_FILE" <<'PYEOF'
import sys, json
src, last_end, frames_path, bstart_path, bend_path, n_path = sys.argv[1:7]
last_end = int(last_end)
rows = []
with open(src, 'r') as f:
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        # ts may be a unix int (synthetic fixtures) OR an ISO-8601 string with a Z suffix
        # (the LIVE node emits "2026-06-15T20:51:42Z"). Normalize to unix seconds.
        ts_raw = o.get('ts')
        ts = None
        if isinstance(ts_raw, int):
            ts = ts_raw
        elif isinstance(ts_raw, str):
            s = ts_raw.strip().replace('Z', '+00:00')
            try:
                if 'T' in s:
                    from datetime import datetime
                    ts = int(datetime.fromisoformat(s).timestamp())
                else:
                    ts = int(float(s))
            except Exception:
                ts = None
        if ts is None:         # missing / unparseable clock -> drop (clean_ts discipline)
            continue
        if ts < 1000000000:    # pre-NTP / unscrubbed outlier -> drop
            continue
        if ts <= last_end:     # already covered by a prior signed batch
            continue
        # AIS decode fields are nested under "msg" on the live node; fall back to flat (fixtures).
        m = o.get('msg')
        if not isinstance(m, dict):
            m = o
        mmsi = m.get('mmsi', '')
        typ  = m.get('type', '')
        lat  = m.get('lat', '')
        lon  = m.get('lon', '')
        rows.append((ts, str(mmsi), str(typ), str(lat), str(lon)))

if not rows:
    print('NOROWS')
    sys.exit(0)

# deterministic order: (ts, mmsi)
rows.sort(key=lambda r: (r[0], r[1]))
lines = []
for ts, mmsi, typ, lat, lon in rows:
    lines.append('mmsi=%s|type=%s|lat=%s|lon=%s|ts=%s' % (mmsi, typ, lat, lon, ts))
with open(frames_path, 'w') as f:
    f.write('\n'.join(lines))
bstart = rows[0][0]
bend   = rows[-1][0]
n      = len(rows)
with open(bstart_path, 'w') as f: f.write(str(bstart))
with open(bend_path,  'w') as f: f.write(str(bend))
with open(n_path,     'w') as f: f.write(str(n))
print('ROWS %d %d %d' % (n, bstart, bend))
PYEOF
)"

case "$SUMMARY" in
    NOROWS*)
        echo "ROLLUP: no new rows since batch_end=$LAST_END -- idempotent no-op (exit 0)"
        exit 0
        ;;
    ROWS*)
        : # fall through to sign
        ;;
    *)
        echo "ROLLUP_ERR: batch builder failed: $SUMMARY" >&2
        exit 2
        ;;
esac
BATCH_END="$(echo "$SUMMARY" | awk '{print $4}')"
echo "ROLLUP: staged batch $SUMMARY (last_end was $LAST_END)"

# --- Stage prev= (the tail line's chain_hash) for real hash-chaining -------------------------
PREV="GENESIS"
if [ -f "$LEDGER" ]; then
    TAILHASH="$(tail -1 "$LEDGER" 2>/dev/null | "$PY" -c "
import sys,json
line=sys.stdin.read().strip()
if line:
    try: print(json.loads(line).get('chain_hash',''))
    except Exception: pass
" 2>/dev/null)"
    if [ -n "$TAILHASH" ]; then
        PREV="$TAILHASH"
    fi
fi
printf '%s\n' "$PREV" > "$PREV_FILE"

# --- Fetch the live beacon pulse (honest PENDING fallback on any failure) --------------------
# fetch_beacon_pulse.sh owns the single curl; it stages /tmp/lg_pulse_id.txt + /tmp/lg_pulse_hex.txt.
source "$REPO/scripts/fetch_beacon_pulse.sh"

# --- A.6 custody (rung 3): stage the code + input digests the signer commits to --------------
# code_sha256 = sha256 of the signer SOURCE (binds WHICH code produced the product; source text,
# not the compiled binary -- reproducible-build is a future rung). input_sha256 = sha256 of the
# raw AIS source bytes this batch decoded from (matches transit_log.py src_ais_sha256). Hex or an
# honest PENDING_* -- NEVER 0, NEVER fabricated. Same hex-or-PENDING discipline as the pulse fetch.
CODE_SHA="$(shasum -a 256 "$REPO/src/ais_attest.rail" 2>/dev/null | awk '{print $1}')"
case "$CODE_SHA" in ''|*[!0-9a-fA-F]*) CODE_SHA="PENDING_no_code_hash" ;; esac
printf '%s\n' "$CODE_SHA" > /tmp/ais_code_sha256.txt
INPUT_SHA="$(shasum -a 256 "$AIS_SRC" 2>/dev/null | awk '{print $1}')"
case "$INPUT_SHA" in ''|*[!0-9a-fA-F]*) INPUT_SHA="PENDING_no_input_hash" ;; esac
printf '%s\n' "$INPUT_SHA" > /tmp/ais_input_sha256.txt

# --- Invoke the pure-Rail signer via the flock-serialized wrapper ----------------------------
# The signer reads all the /tmp staging files, signs, self-verifies, appends the v=2 line to the
# ledger, rewrites the legacy single-object, and writes data/ais_fact_chain.txt.
bash "$REPO/scripts/railrun.sh" "$REPO/src/ais_attest.rail"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "ROLLUP_ERR: signer railrun.sh exited $RC -- ledger NOT advanced" >&2
    exit "$RC"
fi

# --- Advance the cursor ONLY after a successful sign -----------------------------------------
printf '%s\n' "$BATCH_END" > "$CURSOR"
echo "ROLLUP: signed + chained; cursor advanced to batch_end=$BATCH_END"
exit 0
