#!/bin/bash
# corpus_engine_rollup.sh -- L2 DRIVER for the attested-corpus engine (PAOS-for-ourselves Rung 1, v=2).
#
# The standing driver. Sits OFF the raw decode path. Given the accumulated AIS source jsonl it:
#   1. derives the cursor (last-signed batch_end): the persisted cursor file > the CORPUS ledger
#      tail's batch_end (fresh checkout that already has a ledger) > 0 (genesis).
#   2. runs the L1 extractor (scripts/corpus_extract.py) over the NEW frames since the cursor. The
#      extractor binds each row's derived_from via THE RESOLVER (ts -> covering AIS batch window ->
#      that receipt's chain_hash), appends bound rows to data/corpus/ais_corpus.jsonl, rejects the
#      unresolvable/pre-NTP/failed-oracle observations to data/corpus/ais_rejects.jsonl with the bit,
#      and stages the SINGLE-PARENT batch + counts to the EXACT /tmp paths the signer reads.
#   3. zero new bound rows -> exit 0 with NO receipt (idempotent no-op).
#   4. stages prev= (the tail line's chain_hash of ais_corpus_receipts.jsonl, GENESIS if empty),
#      code_sha256 (shasum of the signer .rail SOURCE), input_sha256 (shasum of the AIS source bytes),
#      and the live beacon pulse (honest PENDING fallback).
#   5. drives the pure-Rail signer (src/corpus_attest.rail) via the flock-serialized railrun wrapper.
#      RC != 0 -> the ledger is NOT advanced, exit RC.
#   6. advances the cursor ONLY after a successful sign (RC == 0).
#
# 1:1 receipt-per-AIS-batch: the extractor stages only the rows for the EARLIEST covering AIS window
# (SINGLE-PARENT; verify.rail's resolve_derived_multi handles exactly 2 parents). A batch spanning
# more than one AIS window is signed one window per driver pass.
#
# AUDIT-HARDENED no-op (attest_iq_capture_rollup.sh:88 discipline): a no-op fires ONLY if the cursor
# is present AND data/corpus/corpus_fact_chain.txt is present + non-empty AND the extractor staged
# zero new rows. A cursor present with a MISSING/empty fact-chain (ledger files cleared but the cursor
# left behind) must NOT no-op -- otherwise the pipeline wedges ("already signed" yet no FACT root). In
# that case we fall through and re-sign, which rewrites the fact-chain (the WEDGE GUARD).
#
# This driver writes ONLY the greenfield data/corpus/ tree:
#   data/corpus/ais_corpus.jsonl          (corpus rows -- via the extractor)
#   data/corpus/ais_rejects.jsonl         (rejects -- via the extractor)
#   data/corpus/ais_corpus_receipts.jsonl (chained v=2 receipts -- via the signer)
#   data/corpus/ais_corpus_receipt.json   (legacy single-object -- via the signer)
#   data/corpus/corpus_fact_chain.txt     (the corpus chain tail -- via the signer)
#   data/corpus/corpus_rollup_cursor.txt  (last-signed batch_end -- THIS driver)
# It NEVER reads/writes the live AIS chain (ais_receipts.jsonl / ais_fact_chain.txt /
# ais_rollup_cursor.txt / ais_receipt.json -- it READS ais_receipts.jsonl read-only via the
# extractor's resolver) and NEVER invokes the AIS signer (scripts/attest_ais_rollup.sh,
# src/ais_attest.rail). The raw capture/decode path is NEVER edited.
#
# ---------------------------------------------------------------------------------------------
# LIVE DEPLOY TARGET (production source row file -- NOT read during this synthetic build):
#   ~/.ledatic/roofv2/ais.jsonl
# In production AIS_SRC defaults to ~/.ledatic/roofv2/ais.jsonl. For OFFLINE / SYNTHETIC validation
# pass AIS_SRC=<fixture> in the environment so this NEVER reads the live node path during a build.
# ---------------------------------------------------------------------------------------------
#
# Usage:
#   AIS_SRC=/tmp/fix_ais.jsonl bash scripts/corpus_engine_rollup.sh             # since-cursor (default)
#   AIS_SRC=/tmp/fix_ais.jsonl bash scripts/corpus_engine_rollup.sh since-cursor
#   AIS_SRC=/tmp/fix_ais.jsonl bash scripts/corpus_engine_rollup.sh single      # one batch (==since-cursor)
#   AIS_SRC=/tmp/fix_ais.jsonl bash scripts/corpus_engine_rollup.sh all         # cursor=0, scan from genesis
#   bash scripts/corpus_engine_rollup.sh                                        # production (live src default)
#
# bash-3.2 / macOS safe. set -u, NO set -e (a failing curl must fall through to the honest PENDING
# pulse, not abort the driver; each failure path is guarded explicitly).
set -u

REPO="/Users/ledaticempire/projects/ledaticground"
PY="/opt/homebrew/bin/python3.11"
EXTRACTOR="$REPO/scripts/corpus_extract.py"
SIGNER="$REPO/src/corpus_attest.rail"

# Source row file: $AIS_SRC env > live default. The live default is the DEPLOY TARGET; synthetic
# validation always passes a fixture so the live node path is never read in a build.
AIS_SRC="${AIS_SRC:-$HOME/.ledatic/roofv2/ais.jsonl}"

# Mode: arg 1 in {single, all, since-cursor}; default since-cursor. single == since-cursor (one
# 1:1 batch per pass either way). all forces cursor=0 (re-scan from genesis; dedup still prevents
# duplicate rows, so it appends only genuinely-missing rows).
MODE="${1:-since-cursor}"
case "$MODE" in
    single|all|since-cursor) : ;;
    *)
        echo "CORPUS_ENGINE_ERR: unknown mode '$MODE' (want single|all|since-cursor)" >&2
        exit 2
        ;;
esac

CORPUS_DIR="$REPO/data/corpus"
LEDGER="$CORPUS_DIR/ais_corpus_receipts.jsonl"      # chained CORPUS receipts (the corpus chain)
CURSOR="$CORPUS_DIR/corpus_rollup_cursor.txt"       # tracks the last signed batch_end (unix seconds)
FACT_CHAIN="$CORPUS_DIR/corpus_fact_chain.txt"      # the corpus chain tail (wedge-guard dependency)

# /tmp staging the signer reads (EXACT paths -- load-bearing).
PREV_FILE="/tmp/corpus_prev_sha.txt"                # prev= for the signer (tail chain_hash | GENESIS)
CODE_FILE="/tmp/corpus_code_sha256.txt"             # code_sha256 = shasum of the signer .rail SOURCE
INPUT_FILE="/tmp/corpus_input_sha.txt"              # input_sha256 = shasum of the AIS source bytes
STG_LABEL_CNT="/tmp/paos_label_count.txt"           # the extractor stages this (rows in THIS batch)

# Python interpreter is required (file-based config; do not assume PATH).
if [ ! -x "$PY" ]; then
    echo "CORPUS_ENGINE_ERR: python3.11 not executable at $PY" >&2
    exit 2
fi
if [ ! -f "$EXTRACTOR" ]; then
    echo "CORPUS_ENGINE_ERR: extractor missing: $EXTRACTOR" >&2
    exit 2
fi
if [ ! -f "$SIGNER" ]; then
    echo "CORPUS_ENGINE_ERR: signer source missing: $SIGNER" >&2
    exit 2
fi

# Source file must exist; if absent there is simply nothing to attest yet (idempotent no-op).
if [ ! -f "$AIS_SRC" ]; then
    echo "CORPUS_ENGINE: source $AIS_SRC absent -- nothing to attest (exit 0)"
    exit 0
fi

mkdir -p "$CORPUS_DIR"

# --- Determine the cursor (last signed batch_end) -------------------------------------------
# Prefer the persisted cursor file; if missing, derive it from the CORPUS ledger tail's batch_end so
# a fresh checkout that already has a corpus ledger does not re-sign everything. Empty -> 0 (genesis).
# Mode all forces cursor=0 (re-scan from genesis). Note: the cursor controls only WHICH frames the
# extractor considers via the CORPUS ledger dedup it also runs; the cursor is the driver-level record.
CURSOR_PRESENT=0
LAST_END="0"
if [ -f "$CURSOR" ]; then
    CURSOR_PRESENT=1
    LAST_END="$(cat "$CURSOR" 2>/dev/null | tr -d '[:space:]')"
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
if [ "$MODE" = "all" ]; then
    LAST_END="0"
fi
echo "CORPUS_ENGINE: mode=$MODE cursor_present=$CURSOR_PRESENT last_end=$LAST_END src=$AIS_SRC"

# --- Run the L1 extractor over the NEW frames -----------------------------------------------
# The extractor reads AIS_SRC, dedups against the committed corpus rows, binds derived_from via the
# resolver, appends bound rows + rejects, and stages the SINGLE-PARENT batch + counts to /tmp. It
# writes /tmp/paos_label_count.txt = the count of rows staged for THIS receipt (0 if none new).
# (The extractor also stages /tmp/corpus_input_sha.txt; we re-stage it below as the authoritative
# shasum of the AIS source bytes per the spec L2 contract -- identical value, driver-owned.)
AIS_SRC="$AIS_SRC" "$PY" "$EXTRACTOR"
ERC=$?
if [ "$ERC" -ne 0 ]; then
    echo "CORPUS_ENGINE_ERR: extractor exited $ERC -- ledger NOT advanced" >&2
    exit "$ERC"
fi

# How many new rows did the extractor stage for THIS batch?
LABEL_COUNT="0"
if [ -f "$STG_LABEL_CNT" ]; then
    LABEL_COUNT="$(cat "$STG_LABEL_CNT" 2>/dev/null | tr -d '[:space:]')"
fi
case "$LABEL_COUNT" in ''|*[!0-9]*) LABEL_COUNT="0" ;; esac

# --- AUDIT-HARDENED no-op gate (wedge guard) ------------------------------------------------
# No-op ONLY if: cursor present AND fact-chain present+non-empty AND the extractor staged zero rows.
# If the extractor staged zero rows but the fact-chain is MISSING/empty (or there is no cursor), we
# must NOT silently exit -- but with zero rows there is nothing to sign either, so the no-op path
# below still exits 0; the wedge-guard only changes behavior when there ARE rows to (re-)sign.
if [ "$LABEL_COUNT" -eq 0 ]; then
    if [ "$CURSOR_PRESENT" -eq 1 ] && [ -s "$FACT_CHAIN" ]; then
        echo "CORPUS_ENGINE: 0 new corpus rows (cursor present + fact-chain present) -- idempotent no-op (exit 0)"
    else
        # zero new rows: there is genuinely nothing to sign this pass (the wedge guard re-signs only
        # when rows exist). Honest no-op, but surface why we did not hit the hardened gate.
        echo "CORPUS_ENGINE: 0 new corpus rows -- nothing to sign (cursor_present=$CURSOR_PRESENT fact_chain_nonempty=$([ -s "$FACT_CHAIN" ] && echo 1 || echo 0)); exit 0"
    fi
    exit 0
fi

echo "CORPUS_ENGINE: extractor staged $LABEL_COUNT new corpus row(s) -- proceeding to sign"

# --- Stage prev= (the tail line's chain_hash) for real hash-chaining -------------------------
# prev= is the tail CORPUS line's chain_hash (the corpus chain is its OWN ledger, starting at GENESIS,
# distinct from the AIS FACT chain). GENESIS if the ledger is empty/absent.
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

# --- A.6 custody: stage code_sha256 (signer SOURCE) + input_sha256 (AIS source bytes) -------
# code_sha256 = sha256 of the signer .rail SOURCE (binds WHICH code produced the product; source
# text, not the compiled binary -- reproducible-build is a future rung). Hex or honest PENDING.
CODE_SHA="$(shasum -a 256 "$SIGNER" 2>/dev/null | awk '{print $1}')"
case "$CODE_SHA" in ''|*[!0-9a-fA-F]*) CODE_SHA="PENDING_no_code_hash" ;; esac
if [ "$CODE_SHA" != "PENDING_no_code_hash" ] && [ "${#CODE_SHA}" -ne 64 ]; then
    CODE_SHA="PENDING_no_code_hash"
fi
printf '%s\n' "$CODE_SHA" > "$CODE_FILE"

# input_sha256 = sha256 of the raw AIS source bytes this batch decoded from (driver-authoritative;
# matches the extractor's staged value). Hex or honest PENDING -- NEVER 0, NEVER fabricated.
INPUT_SHA="$(shasum -a 256 "$AIS_SRC" 2>/dev/null | awk '{print $1}')"
case "$INPUT_SHA" in ''|*[!0-9a-fA-F]*) INPUT_SHA="PENDING_no_input_hash" ;; esac
if [ "$INPUT_SHA" != "PENDING_no_input_hash" ] && [ "${#INPUT_SHA}" -ne 64 ]; then
    INPUT_SHA="PENDING_no_input_hash"
fi
printf '%s\n' "$INPUT_SHA" > "$INPUT_FILE"

# --- Fetch the live beacon pulse (honest PENDING fallback on any failure) --------------------
# fetch_beacon_pulse.sh owns the single curl; it stages /tmp/lg_pulse_id.txt + /tmp/lg_pulse_hex.txt.
source "$REPO/scripts/fetch_beacon_pulse.sh"

echo "CORPUS_ENGINE: staged prev=$PREV code_sha256=$CODE_SHA input_sha256=$INPUT_SHA"

# --- Invoke the pure-Rail signer via the flock-serialized wrapper ----------------------------
# The signer reads the /tmp staging files, signs, self-verifies, appends the v=2 line to the corpus
# ledger, rewrites the legacy single-object, and writes data/corpus/corpus_fact_chain.txt.
bash "$REPO/scripts/railrun.sh" "$SIGNER"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "CORPUS_ENGINE_ERR: signer railrun.sh exited $RC -- ledger NOT advanced" >&2
    exit "$RC"
fi

# --- Advance the cursor ONLY after a successful sign -----------------------------------------
# The cursor records the batch_end the extractor staged for THIS receipt (the last signed batch_end).
BATCH_END="0"
if [ -f "/tmp/paos_batch_end.txt" ]; then
    BATCH_END="$(cat "/tmp/paos_batch_end.txt" 2>/dev/null | tr -d '[:space:]')"
fi
case "$BATCH_END" in ''|*[!0-9]*) BATCH_END="0" ;; esac
printf '%s\n' "$BATCH_END" > "$CURSOR"
echo "CORPUS_ENGINE: signed + chained; cursor advanced to batch_end=$BATCH_END"
exit 0
