#!/bin/bash
# verify_binding.sh -- WAVE C cold-verify driver for a PHYSICS_BINDING_RECEIPT (physicify with teeth).
# RECEIPT_CONTRACT.md A.7 is the byte-exact SSOT for the receipt this re-runs.
#
# THE POINT (vs the plain ledger walk): a PHYSICS_BINDING_RECEIPT carries a single RECORDED number --
# residual_hz, the Hz gap between the MEASURED carrier-centroid Doppler and the SGP4-PREDICTED Doppler
# at the committed alignment. A perfectly valid Ed25519 signature proves only "the signer wrote this
# number"; it does NOT prove the number is the one the cited orbit+geo+track actually yields. Wave C's
# verify.rail RE-RUNS the physics (the inlined doppler_range.rail SGP4 query) at the committed
# alignment and REJECTS ("physics residual unreproducible") if the recomputed residual drifts from the
# committed residual_hz by more than repro_tol_hz -- EVEN WHEN the signature is valid. So forging a
# passing binding now also requires forging a TLE+measured-track whose SGP4 single-point residual
# matches the fabricated number AND whose sha256 digests match the cited tle_sha256/meas_sha256.
# THE CLAIM stays "the cost of an undetected lie scales with the conspiracy required" -- never "proof
# of truth". physics_ok is the recorded MECHANISM bit; this re-check does not reinterpret it.
#
# WHAT THIS HARNESS STAGES (the four inputs verify.rail's physics_recompute reads, all /tmp/lg_verify_*):
#   /tmp/lg_verify_target.txt  -> a SINGLE-LINE binding ledger (the one receipt being re-run)
#   /tmp/lg_verify_facts.txt   -> the iq_capture FACT ledger (resolves the binding's derived_from root)
#   /tmp/lg_verify_tle.txt     -> the cited TLE (l1\nl2); its sha256 MUST equal the receipt's tle_sha256
#   /tmp/lg_verify_meas.txt    -> the cited measured track (doppler_real DOP lines); sha256 == meas_sha256
#   /tmp/lg_verify_times.txt   -> "snap <unix>" rows for the measured windows (eval index -> t_eval_unix)
# then runs src/verify.rail via the flock-serialized railrun.sh.
#
# ONE BINDING LINE AT A TIME: each binding line cites its OWN TLE (a positive line names the right
# orbit, a wrong-tle negative line names a different bird). A single staged TLE only satisfies one
# line's tle_sha256, so we walk a SINGLE-LINE ledger containing exactly the target receipt. (The full
# multi-line ledger walk -- chain/prev/derived_from across lines -- is what the plain verify.rail walk
# in attest_binding_rollup / the regression test already covers; THIS harness is the physics re-run.)
#
# DEFAULT artifacts = the /tmp/binding_* files the most recent attest_binding_rollup.sh left (they
# match the LAST committed binding line). Override per-line with --tle-file/--meas-file/--times-file.
#
# *** LIVE AIS CHAIN: HANDS OFF. *** This driver reads ONLY the binding + iq_capture ledgers and the
# /tmp/binding_* + /tmp/lg_verify_* staging files. It NEVER reads/writes the live AIS ledger
# (ais_receipts.jsonl / ais_fact_chain.txt / ais_rollup_cursor.txt / ais_receipt.json), NEVER runs the
# AIS signer, and writes NOTHING under data/ (it only stages /tmp and prints the verdict).
#
# macOS / bash 3.2. set -u (NOT set -e: guarded failure paths print a clear BINDV_ERR and exit nonzero).
set -u

REPO="/Users/ledaticempire/projects/ledaticground"
VERIFY="$REPO/src/verify.rail"
RAILRUN="$REPO/scripts/railrun.sh"

BIND_LEDGER="$REPO/data/physics_binding_receipts.jsonl"
FACT_LEDGER="$REPO/data/iq_capture_receipts.jsonl"

# default cited artifacts: the most recent rollup's staged files (match the LAST binding line).
TLE_L1_FILE="/tmp/binding_tle_l1.txt"
TLE_L2_FILE="/tmp/binding_tle_l2.txt"
MEAS_FILE="/tmp/binding_meas.out"
TIMES_FILE="/tmp/binding_times.txt"
# combined TLE file override (l1\nl2 in one file) if given.
TLE_FILE=""
LINE_NO=""          # 1-based ledger line to verify; default = last line
TARGET_LEDGER_IN="" # override the binding ledger to read the line FROM (e.g. a fabricated fixture)

while [ $# -gt 0 ]; do
    case "$1" in
        --line)       LINE_NO="${2:-}"; shift 2 ;;
        --ledger)     TARGET_LEDGER_IN="${2:-}"; shift 2 ;;
        --tle-file)   TLE_FILE="${2:-}"; shift 2 ;;
        --tle-l1-file) TLE_L1_FILE="${2:-}"; shift 2 ;;
        --tle-l2-file) TLE_L2_FILE="${2:-}"; shift 2 ;;
        --meas-file)  MEAS_FILE="${2:-}"; shift 2 ;;
        --times-file) TIMES_FILE="${2:-}"; shift 2 ;;
        -h|--help)
            grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "BINDV_ERR: unknown arg: $1" >&2; exit 2 ;;
    esac
done

[ -n "$TARGET_LEDGER_IN" ] && BIND_LEDGER="$TARGET_LEDGER_IN"

if [ ! -f "$VERIFY" ]; then echo "BINDV_ERR: verifier missing: $VERIFY" >&2; exit 2; fi
if [ ! -f "$BIND_LEDGER" ]; then echo "BINDV_ERR: binding ledger missing: $BIND_LEDGER" >&2; exit 2; fi
if [ ! -f "$FACT_LEDGER" ]; then echo "BINDV_ERR: iq_capture FACT ledger missing: $FACT_LEDGER" >&2; exit 2; fi

# --- pick the target binding line (default = last non-empty line) ---
TOTAL_LINES="$(grep -c 'PHYSICS_BINDING_RECEIPT' "$BIND_LEDGER" 2>/dev/null)"
TOTAL_LINES=${TOTAL_LINES:-0}
if [ "$TOTAL_LINES" -lt 1 ]; then echo "BINDV_ERR: no PHYSICS_BINDING_RECEIPT lines in $BIND_LEDGER" >&2; exit 2; fi
if [ -z "$LINE_NO" ]; then LINE_NO="$TOTAL_LINES"; fi
case "$LINE_NO" in ''|*[!0-9]*) echo "BINDV_ERR: bad --line: $LINE_NO" >&2; exit 2 ;; esac
if [ "$LINE_NO" -lt 1 ] || [ "$LINE_NO" -gt "$TOTAL_LINES" ]; then
    echo "BINDV_ERR: --line $LINE_NO out of range (1..$TOTAL_LINES)" >&2; exit 2
fi

TARGET_JLINE="$(grep 'PHYSICS_BINDING_RECEIPT' "$BIND_LEDGER" | sed -n "${LINE_NO}p")"
if [ -z "$TARGET_JLINE" ]; then echo "BINDV_ERR: could not read binding line $LINE_NO" >&2; exit 2; fi

# --- write a SINGLE-LINE binding ledger as the verify.rail target ---
# IMPORTANT: the target line's prev= is preserved verbatim; for a single-line walk verify.rail expects
# line[0].prev == GENESIS. A committed line with a non-GENESIS prev (the chain link is checked by the
# FULL-ledger walk, not here) would trip the prev gate. So we walk it with the line's OWN prev as the
# expected genesis by rewriting nothing -- instead we let verify.rail's walk treat THIS as line 0 and,
# if the receipt's prev != GENESIS, the harness rewrites the EXPECTED prev by trimming: we DON'T touch
# the signed receipt string (that would break the sig). We simply note in output if prev!=GENESIS so the
# operator knows the multi-line chain link is covered by the full walk, not this physics harness.
SINGLE="/tmp/lg_verify_binding_one.jsonl"
printf '%s\n' "$TARGET_JLINE" > "$SINGLE"

# --- extract the receipt's cited digests + claimed orbit for an operator sanity echo ---
get_pipe() { printf '%s\n' "$TARGET_JLINE" | sed -n "s/.*|$1=\([^|\"]*\).*/\1/p" | head -1; }
CITED_TLE_SHA="$(get_pipe tle_sha256)"
CITED_MEAS_SHA="$(get_pipe meas_sha256)"
CITED_RESIDUAL="$(get_pipe residual_hz)"
CITED_PHYSICS_OK="$(get_pipe physics_ok)"
CITED_SAT="$(get_pipe sat)"
CITED_PREV="$(get_pipe prev)"

# --- stage the cited TLE (l1\nl2). printf '%s\n%s\n' (matches the rollup's tle_sha256 input) ---
if [ -n "$TLE_FILE" ]; then
    cp "$TLE_FILE" /tmp/lg_verify_tle.txt
else
    if [ ! -f "$TLE_L1_FILE" ] || [ ! -f "$TLE_L2_FILE" ]; then
        echo "BINDV_ERR: TLE line files missing ($TLE_L1_FILE / $TLE_L2_FILE) -- pass --tle-file or run the rollup first" >&2
        exit 2
    fi
    L1="$(sed -n '1p' "$TLE_L1_FILE")"; L2="$(sed -n '1p' "$TLE_L2_FILE")"
    printf '%s\n%s\n' "$L1" "$L2" > /tmp/lg_verify_tle.txt
fi

# --- stage the cited measured track + the snapshot times ---
if [ ! -f "$MEAS_FILE" ]; then echo "BINDV_ERR: measured-track file missing: $MEAS_FILE" >&2; exit 2; fi
if [ ! -f "$TIMES_FILE" ]; then echo "BINDV_ERR: snapshot-times file missing: $TIMES_FILE" >&2; exit 2; fi
cp "$MEAS_FILE"  /tmp/lg_verify_meas.txt
cp "$TIMES_FILE" /tmp/lg_verify_times.txt

# --- point verify.rail at the single-line binding ledger + the iq_capture FACT ledger ---
printf '%s\n' "$SINGLE"      > /tmp/lg_verify_target.txt
printf '%s\n' "$FACT_LEDGER" > /tmp/lg_verify_facts.txt

# --- operator echo: what the receipt cited vs what we staged (digest match is verify.rail's job) ---
STAGED_TLE_SHA="$(shasum -a 256 /tmp/lg_verify_tle.txt | awk '{print $1}')"
STAGED_MEAS_SHA="$(shasum -a 256 /tmp/lg_verify_meas.txt | awk '{print $1}')"
echo "BINDV: verifying binding line $LINE_NO/$TOTAL_LINES  sat=$CITED_SAT physics_ok=$CITED_PHYSICS_OK residual_hz=$CITED_RESIDUAL"
echo "BINDV: cited  tle_sha256=$CITED_TLE_SHA"
echo "BINDV: staged tle_sha256=$STAGED_TLE_SHA  $( [ "$CITED_TLE_SHA" = "$STAGED_TLE_SHA" ] && echo MATCH || echo MISMATCH )"
echo "BINDV: cited  meas_sha256=$CITED_MEAS_SHA"
echo "BINDV: staged meas_sha256=$STAGED_MEAS_SHA  $( [ "$CITED_MEAS_SHA" = "$STAGED_MEAS_SHA" ] && echo MATCH || echo MISMATCH )"
if [ "$CITED_PREV" != "GENESIS" ]; then
    echo "BINDV: NOTE line prev=$CITED_PREV (non-GENESIS) -- the cross-line chain link is checked by the FULL"
    echo "BINDV:      ledger walk (regression test / rollup), not this single-line physics harness; verify.rail"
    echo "BINDV:      walks this as line 0 so the prev gate will report a link mismatch -- IGNORE link= here and"
    echo "BINDV:      read the physics= verdict. Pass --line 1 (a GENESIS line) for a fully-green single-line walk."
fi

# --- run the cold verifier (the physics re-run happens inside verify.rail) ---
echo "BINDV: running verify.rail (physics re-run inside)"
bash "$RAILRUN" "$VERIFY"
RC=$?
echo "BINDV: verify.rail exit=$RC"
exit "$RC"
