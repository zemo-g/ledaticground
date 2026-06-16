#!/bin/bash
# attest_spectrum_rollup.sh -- SPECTRUM_RECEIPT v=2 roll-up driver (extract-more #2).
# Signs a self-calibrating spectral SUMMARY of a wideband cu8 IQ capture (raw_iq/*.bin -- the real
# 137-band RF environment). Off captures we ALREADY take + retain; complementary to the LRPT-decode
# attestation (that signs the CADUs a capture decodes to; this signs what the band looked like).
#
#   gen_spectrum_summary.py <bin> <fs>  ->  summary JSON + binned spectrum
#   shasum: input (.bin) / spectrum (binned) / product (summary) / code (generator + signer)
#   fetch_beacon_pulse.sh  ->  pulse anchor (honest PENDING on failure)
#   spectrum_attest.rail (via railrun)  ->  appends data/spectrum_receipts.jsonl + legacy + fact_chain
#
# Idempotent (cursor on the .bin hash), with the audit-hardened guard: no-op ONLY if the cursor
# matches AND the fact-chain output is present (a cursor match with a missing fact-chain re-signs).
#
# *** LIVE AIS CHAIN: HANDS OFF. *** Writes ONLY the spectrum ledger family; reads the wideband
# raw_iq corpus read-only; never touches ais_receipts.jsonl / the AIS signer / the AIS decode path.
#
# Usage:
#   bash scripts/attest_spectrum_rollup.sh <capture.bin> [fs_hz]   # one capture
#   bash scripts/attest_spectrum_rollup.sh all                     # every unsigned raw_iq/*.bin
# macOS / bash 3.2 safe. set -u (NOT set -e: the beacon fetch must fall through to honest PENDING).
set -u

REPO="/Users/ledaticempire/projects/ledaticground"
PY="/opt/homebrew/bin/python3.11"
GEN="$REPO/scripts/gen_spectrum_summary.py"
SIGNER="$REPO/src/spectrum_attest.rail"
RAILRUN="$REPO/scripts/railrun.sh"
FETCH="$REPO/scripts/fetch_beacon_pulse.sh"
RAW_DIR="${SPECTRUM_RAW_DIR:-$HOME/.ledatic/roofv2/raw_iq}"

LEDGER="$REPO/data/spectrum_receipts.jsonl"
FACT_CHAIN="$REPO/data/spectrum_fact_chain.txt"
CURSOR="$REPO/data/spectrum_rollup_cursor.txt"   # last-signed input hash(es), one per line

[ -x "$PY" ] || { echo "SPEC_ERR: python3.11 not at $PY" >&2; exit 2; }
mkdir -p "$REPO/data" 2>/dev/null

# ---- filename UTC timestamp (iq_<sat>_el<E>_<MODE>_<YYYYMMDDTHHMMSSZ>.bin) -> unix, else PENDING --
ts_unix() {
  local b stamp
  b="$(basename "$1")"
  # captures stamp as YYYYMMDDThhmmZ (minute precision) or ...hhmmssZ — accept both.
  stamp="$(echo "$b" | grep -oE '[0-9]{8}T[0-9]{4,6}Z' | head -1)"
  [ -z "$stamp" ] && { echo "PENDING"; return; }
  "$PY" -c "
import sys, datetime
s = sys.argv[1]
# strptime is LENIENT about field widths (%H%M%S would mis-eat a 4-digit hhmm as 08:04:05),
# so pick the format by the ACTUAL digit count of the time part, never try-until-no-exception.
tpart = s.split('T', 1)[1].rstrip('Z')
fmt = '%Y%m%dT%H%M%SZ' if len(tpart) == 6 else '%Y%m%dT%H%MZ'
try:
    print(int(datetime.datetime.strptime(s, fmt).replace(tzinfo=datetime.timezone.utc).timestamp()))
except ValueError:
    print('PENDING')
" "$stamp" 2>/dev/null || echo "PENDING"
}

# ---- sha helpers (hex or honest PENDING) ----
sha_of() { local h; h="$(shasum -a 256 "$1" 2>/dev/null | awk '{print $1}')"; case "$h" in [0-9a-f]*) [ ${#h} -eq 64 ] && { echo "$h"; return; };; esac; echo "$2"; }

sign_one() {
  local BIN="$1" FS="${2:-}"
  [ -f "$BIN" ] || { echo "SPEC: $BIN absent -- skip"; return 0; }

  local INPUT_HASH; INPUT_HASH="$(sha_of "$BIN" PENDING_no_input_hash)"
  # idempotency: no-op ONLY if this input is in the cursor AND the fact-chain output exists.
  if [ -f "$CURSOR" ] && [ -s "$FACT_CHAIN" ] && grep -qx "$INPUT_HASH" "$CURSOR" 2>/dev/null; then
    echo "SPEC: $(basename "$BIN") already signed (cursor + fact-chain present) -- idempotent no-op"
    return 0
  fi

  # fs: arg > satdump sidecar samplerate > 250000 default.
  if [ -z "$FS" ]; then
    local LOG; LOG="${BIN%.bin}.satdump.log"
    FS="$(grep -ihoE 'samplerate[^0-9]*[0-9]+' "$LOG" 2>/dev/null | grep -oE '[0-9]+' | head -1)"
    [ -z "$FS" ] && FS=250000
  fi

  echo "SPEC: summarising $(basename "$BIN") (fs=$FS Hz)"
  local SUMM; SUMM="$("$PY" "$GEN" "$BIN" "$FS" 2>/dev/null)" || { echo "SPEC_ERR: generator failed on $BIN" >&2; return 1; }
  # stage the headline scalars (KEY=VAL lines) into /tmp/spectrum_*.txt
  local k v
  echo "$SUMM" | while IFS='=' read -r k v; do
    case "$k" in
      OCC_PCT) printf '%s\n' "$v" > /tmp/spectrum_occ_pct.txt ;;
      PEAK_EXCESS_DB) printf '%s\n' "$v" > /tmp/spectrum_peak_excess_db.txt ;;
      DYN_RANGE_DB) printf '%s\n' "$v" > /tmp/spectrum_dyn_range_db.txt ;;
      N_COLS) printf '%s\n' "$v" > /tmp/spectrum_n_cols.txt ;;
      FS_HZ) printf '%s\n' "$v" > /tmp/spectrum_fs_hz.txt ;;
      NFFT) printf '%s\n' "$v" > /tmp/spectrum_nfft.txt ;;
    esac
  done
  local SUMM_PATH BINS_PATH
  SUMM_PATH="$(echo "$SUMM" | sed -n 's/^SUMMARY_PATH=//p')"
  BINS_PATH="$(echo "$SUMM" | sed -n 's/^BINS_PATH=//p')"
  [ -s "$SUMM_PATH" ] && [ -s "$BINS_PATH" ] || { echo "SPEC_ERR: generator left no summary/bins" >&2; return 1; }

  # custody + hybrid commitments
  printf '%s\n' "$INPUT_HASH" > /tmp/spectrum_input_sha256.txt
  printf '%s\n' "$(sha_of "$BINS_PATH" PENDING_no_spectrum_hash)" > /tmp/spectrum_spectrum_sha256.txt
  printf '%s\n' "$(sha_of "$SUMM_PATH" PENDING_no_product_hash)" > /tmp/spectrum_product_sha256.txt
  # code = the producing toolchain (generator + signer) hashed together
  cat "$GEN" "$SIGNER" 2>/dev/null | shasum -a 256 | awk '{print $1}' > /tmp/spectrum_code_sha256.txt

  # provenance bounds = the capture's filename timestamp
  local T; T="$(ts_unix "$BIN")"
  printf '%s\n' "$T" > /tmp/spectrum_batch_start.txt
  printf '%s\n' "$T" > /tmp/spectrum_batch_end.txt

  # prev = tail chain_hash of the ledger (real hash-chaining)
  local PREV="GENESIS"
  if [ -s "$LEDGER" ]; then
    local TAIL; TAIL="$(tail -1 "$LEDGER" 2>/dev/null | "$PY" -c "import sys,json;l=sys.stdin.read().strip();print(json.loads(l).get('chain_hash','') if l else '')" 2>/dev/null)"
    [ -n "$TAIL" ] && PREV="$TAIL"
  fi
  printf '%s\n' "$PREV" > /tmp/spectrum_prev_sha.txt

  # beacon pulse (honest PENDING on failure)
  # shellcheck disable=SC1090
  [ -f "$FETCH" ] && . "$FETCH"

  bash "$RAILRUN" "$SIGNER"; local RC=$?
  [ "$RC" -ne 0 ] && { echo "SPEC_ERR: signer railrun exited $RC -- not advancing cursor" >&2; return "$RC"; }
  printf '%s\n' "$INPUT_HASH" >> "$CURSOR"
  echo "SPEC: signed + chained $(basename "$BIN")"
  return 0
}

if [ "${1:-}" = "all" ]; then
  n=0
  for BIN in "$RAW_DIR"/*.bin; do
    [ -f "$BIN" ] || continue
    sign_one "$BIN" "" && n=$((n+1))
  done
  echo "SPEC: all -- processed $n capture(s) in $RAW_DIR"
elif [ -n "${1:-}" ]; then
  sign_one "$1" "${2:-}"
else
  echo "usage: $0 <capture.bin> [fs_hz]   |   $0 all" >&2; exit 2
fi
