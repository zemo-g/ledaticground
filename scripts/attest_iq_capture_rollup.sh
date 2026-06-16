#!/bin/bash
# attest_iq_capture_rollup.sh -- v=2 FACT IQ-CAPTURE attestation roll-up driver (ticket B0-CAPTURE).
#
# A NEW, SEPARATE, IDEMPOTENT step OFF the raw decode path. Given a raw IQ .bin, it:
#   1. shasums the .bin for BOTH input_sha256 (custody, A.6) AND product_sha256 (the product
#      of a CAPTURE *is* the raw bytes) -- staged as HEX so no NUL-bearing binary is ever
#      pulled through a Rail string (char_from_int(0)=="" NUL-drop trap).
#   2. shasums the signer's .rail SOURCE for code_sha256 (custody, A.6 -- binds the source text).
#   3. stages the capture-window BOUNDS (batch_start/batch_end) + n=1, and best-effort band.
#   4. fetches the live beacon pulse via fetch_beacon_pulse.sh (honest PENDING on failure).
#   5. stages prev= (the tail line's chain_hash, or GENESIS) for REAL hash-chaining.
#   6. drives the pure-Rail signer via the flock-serialized railrun wrapper.
#   7. advances a cursor keyed on the input hash so RE-RUNNING on the SAME IQ is a no-op.
#
# This driver writes ONLY the SEPARATE iq_capture ledger family:
#   data/iq_capture_receipts.jsonl  (chained v=2)
#   data/iq_capture_receipt.json    (legacy single-object)
#   data/iq_capture_fact_chain.txt  (the derived_from root Wave B uses)
#   data/iq_capture_rollup_cursor.txt  (last-signed input hash, for idempotency)
# It NEVER reads/writes the live AIS chain (ais_receipts.jsonl / ais_fact_chain.txt /
# ais_rollup_cursor.txt / ais_receipt.json) and NEVER invokes the AIS signer.
#
# The raw capture/decode path (pull_iq.sh, pi_iq_capture.sh, refresh.sh) is NEVER edited.
#
# Usage:
#   bash scripts/attest_iq_capture_rollup.sh /abs/path/to/capture.bin
#   IQ_BIN=/abs/path/to/capture.bin bash scripts/attest_iq_capture_rollup.sh
#
# bash-3.2 / macOS safe. No `set -e` (a failing curl must fall through to the honest PENDING
# pulse, not abort the driver). Each failure path is guarded explicitly.
set -u

REPO="/Users/ledaticempire/projects/ledaticground"
SIGNER="$REPO/src/iq_capture_attest.rail"

# IQ .bin path: arg 1 > $IQ_BIN env. There is NO live default -- a capture receipt is always
# over a NAMED .bin (no silent default that could read a live-node path).
IQ_BIN="${1:-${IQ_BIN:-}}"
if [ -z "$IQ_BIN" ]; then
    echo "IQCAP_ERR: no IQ .bin given (arg 1 or \$IQ_BIN)" >&2
    exit 2
fi
if [ ! -f "$IQ_BIN" ]; then
    echo "IQCAP_ERR: IQ .bin not found: $IQ_BIN" >&2
    exit 2
fi
if [ ! -f "$SIGNER" ]; then
    echo "IQCAP_ERR: signer source missing: $SIGNER" >&2
    exit 2
fi

LEDGER="$REPO/data/iq_capture_receipts.jsonl"
CURSOR="$REPO/data/iq_capture_rollup_cursor.txt"   # last-signed input_sha256 (idempotency key)

PRODUCT_FILE="/tmp/iq_capture_product_sha256.txt"
INPUT_FILE="/tmp/iq_capture_input_sha256.txt"
CODE_FILE="/tmp/iq_capture_code_sha256.txt"
PREV_FILE="/tmp/iq_capture_prev_sha.txt"
BSTART_FILE="/tmp/iq_capture_batch_start.txt"
BEND_FILE="/tmp/iq_capture_batch_end.txt"
N_FILE="/tmp/iq_capture_batch_n.txt"
BAND_FILE="/tmp/iq_capture_band.txt"

# shasum must exist (file-based config; do not assume anything but the system tool).
if ! command -v shasum >/dev/null 2>&1; then
    echo "IQCAP_ERR: shasum not on PATH" >&2
    exit 2
fi

# --- Hash the IQ bytes: BOTH input_sha256 AND product_sha256 (a CAPTURE's product IS its bytes)
INPUT_HASH="$(shasum -a 256 "$IQ_BIN" 2>/dev/null | awk '{print $1}')"
case "$INPUT_HASH" in
    [0-9a-fA-F]*) : ;;
    *) echo "IQCAP_ERR: failed to shasum IQ bytes: $IQ_BIN" >&2; exit 2 ;;
esac
if [ "${#INPUT_HASH}" -ne 64 ]; then
    echo "IQCAP_ERR: IQ input hash not 64 hex chars: $INPUT_HASH" >&2
    exit 2
fi
PRODUCT_HASH="$INPUT_HASH"   # product of a capture = the raw bytes (honest identity)

# --- Idempotency: if the cursor already records THIS input hash, this IQ is already signed.
if [ -f "$CURSOR" ]; then
    LAST_INPUT="$(cat "$CURSOR" 2>/dev/null | tr -d '[:space:]')"
    if [ "$LAST_INPUT" = "$INPUT_HASH" ]; then
        echo "IQCAP: input $INPUT_HASH already signed (cursor match) -- idempotent no-op (exit 0)"
        exit 0
    fi
fi

# --- Hash the signer SOURCE for code_sha256 (custody, A.6 -- binds the .rail source text).
CODE_HASH="$(shasum -a 256 "$SIGNER" 2>/dev/null | awk '{print $1}')"
case "$CODE_HASH" in
    [0-9a-fA-F]*) : ;;
    *) CODE_HASH="PENDING_no_code_hash" ;;
esac
if [ "$CODE_HASH" != "PENDING_no_code_hash" ] && [ "${#CODE_HASH}" -ne 64 ]; then
    CODE_HASH="PENDING_no_code_hash"
fi

# --- Capture-window BOUNDS + count. The capture window is the .bin's mtime (provenance of
# WHEN the bytes were captured), scrubbed against the pre-NTP outlier floor (< ~2001 -> PENDING).
# A single .bin is n=1. mtime is a BOUND (provenance), explicitly NOT the attestation clock.
MTIME="$(stat -f %m "$IQ_BIN" 2>/dev/null)"
case "$MTIME" in
    ''|*[!0-9]*) MTIME="PENDING" ;;
    *) if [ "$MTIME" -lt 1000000000 ]; then MTIME="PENDING"; fi ;;
esac
printf '%s\n' "$MTIME" > "$BSTART_FILE"
printf '%s\n' "$MTIME" > "$BEND_FILE"
printf '%s\n' "1"       > "$N_FILE"

# --- Best-effort band from the IQ schedule (NEVER a fabricated frequency; PENDING if unknown).
# data/iq_schedule.tsv maps capture basenames/labels to bands. We only set a band if the .bin
# basename appears verbatim in a schedule row; otherwise honest PENDING.
BAND="IQ-capture-band-PENDING"
SCHED="$REPO/data/iq_schedule.tsv"
BASENAME="$(basename "$IQ_BIN")"
if [ -f "$SCHED" ]; then
    HIT="$(grep -F "$BASENAME" "$SCHED" 2>/dev/null | head -1)"
    if [ -n "$HIT" ]; then
        # take the first whitespace-separated field that looks like a band/freq token; if none,
        # keep PENDING. We do not invent a frequency -- only echo a token the schedule already has.
        CAND="$(printf '%s\n' "$HIT" | awk '{for(i=1;i<=NF;i++){if($i ~ /[Mm][Hh][Zz]/){print $i; exit}}}')"
        if [ -n "$CAND" ]; then
            BAND="$CAND"
        fi
    fi
fi
printf '%s\n' "$BAND" > "$BAND_FILE"

# --- Stage the digests (all HEX strings; the Rail signer reads them via field_or).
printf '%s\n' "$PRODUCT_HASH" > "$PRODUCT_FILE"
printf '%s\n' "$INPUT_HASH"   > "$INPUT_FILE"
printf '%s\n' "$CODE_HASH"    > "$CODE_FILE"

# --- Stage prev= (the tail line's chain_hash) for REAL hash-chaining. GENESIS if empty/absent.
PREV="GENESIS"
PY="/opt/homebrew/bin/python3.11"
if [ -f "$LEDGER" ] && [ -x "$PY" ]; then
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
elif [ -f "$LEDGER" ]; then
    # No python: fall back to a sed/grep extraction of the last line's chain_hash JSON field.
    TAILHASH="$(tail -1 "$LEDGER" 2>/dev/null | sed -n 's/.*"chain_hash": "\([0-9a-fA-F]*\)".*/\1/p')"
    if [ -n "$TAILHASH" ]; then
        PREV="$TAILHASH"
    fi
fi
printf '%s\n' "$PREV" > "$PREV_FILE"

# --- Fetch the live beacon pulse (honest PENDING fallback on any failure) --------------------
# fetch_beacon_pulse.sh owns the single curl; it stages /tmp/lg_pulse_id.txt + /tmp/lg_pulse_hex.txt.
source "$REPO/scripts/fetch_beacon_pulse.sh"

echo "IQCAP: staged capture $BASENAME  input=$INPUT_HASH  band=$BAND  mtime=$MTIME  prev=$PREV"

# --- Invoke the pure-Rail signer via the flock-serialized wrapper ----------------------------
bash "$REPO/scripts/railrun.sh" "$SIGNER"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "IQCAP_ERR: signer railrun.sh exited $RC -- ledger NOT advanced" >&2
    exit "$RC"
fi

# --- Advance the cursor ONLY after a successful sign (records the signed input hash) ---------
printf '%s\n' "$INPUT_HASH" > "$CURSOR"
echo "IQCAP: signed + chained; cursor advanced to input=$INPUT_HASH"
exit 0
