#!/bin/bash
# attest_lrpt_decode_rollup.sh -- v=2 FACT LRPT DECODE-PRODUCT attestation roll-up driver
# (ticket B0-LRPT-DECODE).
#
# A NEW, SEPARATE, IDEMPOTENT step OFF the raw decode path. Given a raw IQ .bin that the live
# decode pipeline (pull_iq.sh -> validate_external.sh -> satdump) has ALREADY deframed, it:
#   1. locates the sibling decode products: <bin>.satdump/meteor_m2-x_lrpt.cadu (the RS-corrected
#      CADU stream) + <bin>.decoded (the marker carrying satdump exit + CADUS).
#   2. shasums the .cadu for product_sha256 (the decode product = the CADU frames) -- staged as
#      HEX so no NUL-bearing binary is pulled through a Rail string (char_from_int(0)=="" trap).
#   3. shasums the raw .bin for input_sha256 (custody A.6). IF the same .bin is ALSO attested by
#      attest_iq_capture_rollup.sh, this digest EQUALS that capture FACT's product_sha256 -- the
#      shared value is the capture<->decode correspondence, cross-checkable by hand or a future
#      verify.rail extension (NOT auto-resolved today; FACTs carry no derived_from). The sweep
#      attest_lrpt_decode_all.sh mints the capture FACT first so the correspondence is populated.
#   4. shasums the signer's .rail SOURCE for code_sha256 (custody A.6 -- binds the source text).
#   5. derives n = CADU count (cadu_bytes/1024) + cadu_ok (1 iff CADUs>0 AND satdump exit=0) --
#      the A.3 honesty bit. A 0-CADU / no-lock pass is STILL signed with cadu_ok=0.
#   6. stages the capture-window BOUNDS (.bin mtime) + best-effort band (schedule, never faked).
#   7. fetches the live beacon pulse via fetch_beacon_pulse.sh (honest PENDING on failure).
#   8. stages prev= (the tail line's chain_hash, or GENESIS) for REAL hash-chaining.
#   9. drives the pure-Rail signer via the flock-serialized railrun wrapper.
#  10. advances a cursor keyed on input:product so RE-RUNNING on an UNCHANGED decode is a no-op,
#      but a re-decode that yields a DIFFERENT product (more CADUs) re-signs.
#
# This driver writes ONLY the SEPARATE lrpt_decode ledger family:
#   data/lrpt_decode_receipts.jsonl   (chained v=2 FACT)
#   data/lrpt_decode_receipt.json     (legacy single-object)
#   data/lrpt_decode_fact_chain.txt   (latest fact chain_hash -- a future image-projection
#                                      INFERENCE can name this as derived_from)
#   data/lrpt_decode_rollup_cursor.txt (last-signed input:product, for idempotency)
# It NEVER reads/writes the live AIS chain or the iq_capture ledger, and NEVER edits the raw
# capture/decode path (pull_iq.sh, validate_external.sh, refresh.sh).
#
# Usage:
#   bash scripts/attest_lrpt_decode_rollup.sh /abs/path/to/iq_*_LRPT_*.bin
#   IQ_BIN=/abs/path/to/iq_*_LRPT_*.bin bash scripts/attest_lrpt_decode_rollup.sh
#
# bash-3.2 / macOS safe. No `set -e` (a failing curl must fall through to the honest PENDING
# pulse, not abort the driver). Each failure path is guarded explicitly.
set -u

REPO="/Users/ledaticempire/projects/ledaticground"
SIGNER="$REPO/src/lrpt_decode_attest.rail"

# IQ .bin path: arg 1 > $IQ_BIN env. There is NO live default -- a decode receipt is always
# over a NAMED .bin (no silent default that could read a live-node path).
IQ_BIN="${1:-${IQ_BIN:-}}"
if [ -z "$IQ_BIN" ]; then
    echo "LRPTDEC_ERR: no IQ .bin given (arg 1 or \$IQ_BIN)" >&2
    exit 2
fi
if [ ! -f "$IQ_BIN" ]; then
    echo "LRPTDEC_ERR: IQ .bin not found: $IQ_BIN" >&2
    exit 2
fi
if [ ! -f "$SIGNER" ]; then
    echo "LRPTDEC_ERR: signer source missing: $SIGNER" >&2
    exit 2
fi

BASENAME="$(basename "$IQ_BIN")"

# This driver is LRPT-ONLY: the capture filename convention is iq_<sat>_el<E>_<MODE>_<ts>.bin,
# and LRPT product attestation is meaningful only for an LRPT pass. Refuse anything else so we
# never sign an APT/ORBCOMM .bin under an LRPT_DECODE receipt.
case "$BASENAME" in
    *_LRPT_*) : ;;
    *) echo "LRPTDEC_ERR: not an LRPT capture (basename lacks _LRPT_): $BASENAME" >&2; exit 2 ;;
esac

# --- Locate the sibling decode products written by the live pipeline -------------------------
SATDIR="${IQ_BIN%.bin}.satdump"
MARKER="${IQ_BIN%.bin}.decoded"
CADU="$SATDIR/meteor_m2-x_lrpt.cadu"   # validate_external.sh's canonical LRPT product path
# Fall back to any *.cadu in the satdump dir if the canonical name ever changes upstream.
if [ ! -f "$CADU" ] && [ -d "$SATDIR" ]; then
    ALT="$(ls "$SATDIR"/*.cadu 2>/dev/null | head -1)"
    if [ -n "$ALT" ]; then CADU="$ALT"; fi
fi

LEDGER="$REPO/data/lrpt_decode_receipts.jsonl"
CURSOR="$REPO/data/lrpt_decode_rollup_cursor.txt"   # last-signed input:product (idempotency key)
FACT_CHAIN="$REPO/data/lrpt_decode_fact_chain.txt"

PRODUCT_FILE="/tmp/lrpt_decode_product_sha256.txt"
INPUT_FILE="/tmp/lrpt_decode_input_sha256.txt"
CODE_FILE="/tmp/lrpt_decode_code_sha256.txt"
CADUOK_FILE="/tmp/lrpt_decode_cadu_ok.txt"
PREV_FILE="/tmp/lrpt_decode_prev_sha.txt"
BSTART_FILE="/tmp/lrpt_decode_batch_start.txt"
BEND_FILE="/tmp/lrpt_decode_batch_end.txt"
N_FILE="/tmp/lrpt_decode_batch_n.txt"
BAND_FILE="/tmp/lrpt_decode_band.txt"

if ! command -v shasum >/dev/null 2>&1; then
    echo "LRPTDEC_ERR: shasum not on PATH" >&2
    exit 2
fi

# --- input_sha256: hash the raw IQ bytes (custody; == the iq_capture FACT's product_sha256) --
INPUT_HASH="$(shasum -a 256 "$IQ_BIN" 2>/dev/null | awk '{print $1}')"
case "$INPUT_HASH" in
    [0-9a-fA-F]*) : ;;
    *) echo "LRPTDEC_ERR: failed to shasum IQ bytes: $IQ_BIN" >&2; exit 2 ;;
esac
if [ "${#INPUT_HASH}" -ne 64 ]; then
    echo "LRPTDEC_ERR: IQ input hash not 64 hex chars: $INPUT_HASH" >&2
    exit 2
fi

# --- CADU product: count, honesty bit, product hash -----------------------------------------
# n = CADU count = cadu_bytes / 1024 (validate_external.sh's exact convention). A CADU present
# IS a Reed-Solomon-corrected CCSDS frame, so count>0 is the verified-decode signal.
CADU_BYTES=0
PRODUCT_HASH="PENDING_no_product_hash"
if [ -f "$CADU" ]; then
    CADU_BYTES="$(stat -f %z "$CADU" 2>/dev/null || stat -c %s "$CADU" 2>/dev/null || echo 0)"
    case "$CADU_BYTES" in ''|*[!0-9]*) CADU_BYTES=0 ;; esac
    if [ "$CADU_BYTES" -gt 0 ]; then
        PH="$(shasum -a 256 "$CADU" 2>/dev/null | awk '{print $1}')"
        if [ "${#PH}" -eq 64 ]; then PRODUCT_HASH="$PH"; fi
    fi
fi
N_CADUS=$(( CADU_BYTES / 1024 ))

# satdump exit from the .decoded marker (provenance of the decode run). PENDING if no marker.
SD_EXIT="PENDING"
if [ -f "$MARKER" ]; then
    # marker carries "... satdump=exit=N | products: CADUS=n cadu_bytes=b"
    E="$(grep -oE 'satdump=exit=[0-9]+' "$MARKER" 2>/dev/null | head -1 | sed 's/.*=//')"
    if [ -n "$E" ]; then SD_EXIT="$E"; fi
fi

# Anti-false-sync discriminator cross-check: satdump locks onto FLAT NOISE and still emits
# RS-shaped CADUs (observed: a "1023-CADU" pass whose own waterfall verdict reads FLAT NOISE).
# The .decoded marker carries an INDEPENDENT waterfall+decode verdict (peak_snr / drift /
# SYNC_LOCK / lines). If that verdict calls the pass noise, the CADUs are a false-sync artifact,
# not a weather decode -- signing cadu_ok=1 over it would assert a decode that did not happen
# (the no-synthetic-evidence line). Detect the discriminator's noise verdict tokens.
SIGNAL_NOISE=0
if [ -f "$MARKER" ]; then
    if grep -qE "FLAT NOISE|\| noise \|" "$MARKER" 2>/dev/null; then SIGNAL_NOISE=1; fi
fi

# cadu_ok (A.3 honesty bit): 1 iff RS-corrected CADUs were produced (count>0) AND satdump
# CONFIRMED a clean exit (exit=0) AND the independent discriminator did NOT call the pass noise.
# FAIL CLOSED otherwise — including a MISSING marker (SD_EXIT=PENDING): pull_iq.sh writes the
# .decoded marker LAST, so a crash between satdump and the marker leaves CADUs on disk with no
# exit proof; per contract A.3 the verdict bit stays 0 (the frames are NOT lost — product_sha256
# + n>0 still commit them; only the unverified clean-decode verdict is withheld). A present
# marker with nonzero exit, OR a noise verdict over false-synced CADUs, also yields 0.
CADU_OK=0
if [ "$N_CADUS" -gt 0 ] && [ "$SD_EXIT" = "0" ] && [ "$SIGNAL_NOISE" = "0" ]; then
    CADU_OK=1
fi
if [ "$N_CADUS" -gt 0 ] && [ "$SIGNAL_NOISE" = "1" ]; then
    echo "LRPTDEC: $N_CADUS CADUs present but discriminator verdict=NOISE -> cadu_ok=0 (false-sync, not a decode)"
fi

# --- DECODED-PAYLOAD PROVENANCE: which satellite + onboard imager config the decode recovered ----
# satdump writes dataset.json (satellite) + telemetry.json (per-frame onboard MSU-MR id/set). We
# attest this ONLY on a genuine decode (cadu_ok=1) and only a value the telemetry agrees on
# UNANIMOUSLY -- so we never assert an instrument config recovered from a noise false-sync, and
# never a mixed/ambiguous one. A noise/0-CADU pass clears the staging -> the signer reads
# PENDING_no_decode. instrument_set = PRIMARY vs BACKUP imager: onboard spacecraft state, attested
# from our own off-air bytes (e.g. METEOR-M2-4 observed flying its BACKUP MSU-MR).
rm -f /tmp/lrpt_decode_sat.txt /tmp/lrpt_decode_instrument_id.txt /tmp/lrpt_decode_instrument_set.txt
# NOTE: literal python path (NOT $PY -- that is defined later in this script; under `set -u` a
# forward-reference would ABORT the rollup before signing). This staging is best-effort: any
# failure leaves the fields at PENDING_no_decode and never disturbs the core LRPT signing.
if [ "$CADU_OK" = "1" ]; then
    /opt/homebrew/bin/python3.11 - "$SATDIR" <<'PYEOF' 2>/dev/null || true
import sys, json, os, collections
sd = sys.argv[1]
def stage(name, val):
    if val is None or val == "": return
    with open("/tmp/lrpt_decode_%s.txt" % name, "w") as f: f.write(str(val))
try:
    sat = json.load(open(os.path.join(sd, "dataset.json"))).get("satellite")
    stage("sat", sat)
except Exception:
    pass
try:
    tm = json.load(open(os.path.join(sd, "telemetry.json")))
    if isinstance(tm, list) and tm:
        ids  = collections.Counter(r.get("msu_mr_id")  for r in tm if isinstance(r, dict))
        sets = collections.Counter(r.get("msu_mr_set") for r in tm if isinstance(r, dict))
        if len(ids)  == 1 and None not in ids:  stage("instrument_id",  next(iter(ids)))   # unanimous only
        if len(sets) == 1 and None not in sets: stage("instrument_set", next(iter(sets)))
except Exception:
    pass
PYEOF
fi

# --- Per-ledger CRITICAL SECTION (concurrency): the global /tmp/railrun.lock only serializes
# compile+run, NOT our read-tail -> stage-prev -> append. Two drivers racing (e.g. a sweep
# overlapping the next 5-min cron cycle, or a manual run racing cron) would read the same tail,
# stage the same prev, and BOTH append -> a forked chain that verify.rail rejects as LEDGER
# INVALID. Hold a dedicated lock across the idempotency check THROUGH the append+cursor advance
# so check-and-append is atomic. flock is the same primitive railrun.sh relies on; auto-released
# on exit. A 120s wait then a clean skip (a concurrent driver is already advancing this ledger).
exec 8>"/tmp/lrpt_decode_ledger.lock"
flock -w 120 8 || { echo "LRPTDEC: ledger lock busy >120s -- another driver is advancing it; skip (exit 0)"; exit 0; }

# --- Idempotency: a LEDGER SCAN (stronger than the iq_capture sibling's single "last-signed"
# cursor, which only guards the most-recent capture). This (input_sha256, product_sha256) pair
# uniquely identifies a (capture, decode-result): input_sha256 is unique per .bin, so even two
# distinct 0-CADU passes (both product=PENDING_no_product_hash) are told apart by their input.
# If a ledger line already carries BOTH fields -> this exact decode is already attested -> no-op.
# Guarded by fact-chain presence (audit finding: a match with a missing fact-chain must re-sign,
# not wedge). The cursor is still advanced after a sign, as a fast human-readable last-signed note.
COMBO="${INPUT_HASH}:${PRODUCT_HASH}"
if [ -f "$LEDGER" ] && [ -s "$FACT_CHAIN" ]; then
    if grep -F "input_sha256=$INPUT_HASH" "$LEDGER" 2>/dev/null | grep -Fq "product_sha256=$PRODUCT_HASH"; then
        echo "LRPTDEC: $BASENAME already attested (ledger has input=$INPUT_HASH product=$PRODUCT_HASH) -- idempotent no-op (exit 0)"
        exit 0
    fi
fi

# --- code_sha256: hash the signer SOURCE (custody A.6 -- binds the .rail source text). --------
CODE_HASH="$(shasum -a 256 "$SIGNER" 2>/dev/null | awk '{print $1}')"
case "$CODE_HASH" in
    [0-9a-fA-F]*) : ;;
    *) CODE_HASH="PENDING_no_code_hash" ;;
esac
if [ "$CODE_HASH" != "PENDING_no_code_hash" ] && [ "${#CODE_HASH}" -ne 64 ]; then
    CODE_HASH="PENDING_no_code_hash"
fi

# --- Capture-window BOUNDS (.bin mtime; provenance of WHEN the bytes were captured, NOT the
# attestation clock). Scrubbed against the pre-NTP outlier floor (-> PENDING). n = CADU count.
MTIME="$(stat -f %m "$IQ_BIN" 2>/dev/null || stat -c %m "$IQ_BIN" 2>/dev/null)"
case "$MTIME" in
    ''|*[!0-9]*) MTIME="PENDING" ;;
    *) if [ "$MTIME" -lt 1000000000 ]; then MTIME="PENDING"; fi ;;
esac
printf '%s\n' "$MTIME"    > "$BSTART_FILE"
printf '%s\n' "$MTIME"    > "$BEND_FILE"
printf '%s\n' "$N_CADUS"  > "$N_FILE"
printf '%s\n' "$CADU_OK"  > "$CADUOK_FILE"

# --- Band from the IQ schedule (NEVER a fabricated frequency; PENDING if the schedule lacks it).
# Schedule columns (tab-separated): AOS_epoch  DUR  ELEV  FREQ_HZ  MODE  SAT. We read FREQ_HZ
# (col 4) from a row whose MODE (col 5) is LRPT and render it in MHz -> e.g. LRPT-137.900MHz.
# This is honest provenance SOURCED FROM the schedule, not a hard-coded literal; PENDING if the
# schedule has no LRPT row. (LRPT downlinks share 137.9 MHz, so the per-pass freq is invariant;
# the receipt's KIND already says LRPT, so band is provenance only, never load-bearing.)
BAND="LRPT-band-PENDING"
SCHED="$REPO/data/iq_schedule.tsv"
if [ -f "$SCHED" ]; then
    HZ="$(awk -F'\t' '$5=="LRPT"{print $4; exit}' "$SCHED" 2>/dev/null)"
    case "$HZ" in
        ''|*[!0-9]*) : ;;
        *) BAND="LRPT-$(awk "BEGIN{printf \"%.3f\", $HZ/1000000}")MHz" ;;
    esac
fi
printf '%s\n' "$BAND" > "$BAND_FILE"

# --- Stage the digests (all HEX strings; the Rail signer reads them via field_or). ------------
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
    if [ -n "$TAILHASH" ]; then PREV="$TAILHASH"; fi
elif [ -f "$LEDGER" ]; then
    TAILHASH="$(tail -1 "$LEDGER" 2>/dev/null | sed -n 's/.*"chain_hash": "\([0-9a-fA-F]*\)".*/\1/p')"
    if [ -n "$TAILHASH" ]; then PREV="$TAILHASH"; fi
fi
printf '%s\n' "$PREV" > "$PREV_FILE"

# --- Fetch the live beacon pulse (honest PENDING fallback on any failure) --------------------
# fetch_beacon_pulse.sh owns the single curl; it stages /tmp/lg_pulse_id.txt + /tmp/lg_pulse_hex.txt.
source "$REPO/scripts/fetch_beacon_pulse.sh"

echo "LRPTDEC: staged $BASENAME  cadus=$N_CADUS  cadu_ok=$CADU_OK  satdump_exit=$SD_EXIT  input=$INPUT_HASH  product=$PRODUCT_HASH  band=$BAND  prev=$PREV"

# --- Invoke the pure-Rail signer via the flock-serialized wrapper ----------------------------
bash "$REPO/scripts/railrun.sh" "$SIGNER"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "LRPTDEC_ERR: signer railrun.sh exited $RC -- ledger NOT advanced" >&2
    exit "$RC"
fi

# --- Advance the cursor ONLY after a successful sign (records the signed input:product) -------
printf '%s\n' "$COMBO" > "$CURSOR"
echo "LRPTDEC: signed + chained; cursor advanced to $COMBO"
exit 0
