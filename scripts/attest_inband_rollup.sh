#!/bin/bash
# attest_inband_rollup.sh — the shared Mini-side roll-up driver for the two IN-BAND
# streams (ACARS 136.7–136.975 MHz, ORBCOMM 137.2–137.8 MHz). Implements RECEIPT_CONTRACT.md
# sections (C),(F): it fetches the live beacon pulse ONCE, then drives both Rail signers
# (src/acars_attest.rail + src/orbcomm_attest.rail) via the flock-serialized railrun.sh,
# appending one chained line per stream to data/<stream>_receipts.jsonl.
#
# It is a SEPARATE, idempotent roll-up step. It reads the decode PRODUCTS the radio
# cluster's decode step writes (/tmp/acars_deframe_out.txt, /tmp/orbcomm_decode_out.txt)
# and NEVER touches the raw capture / decode path (ais_monitor.sh, pull_iq.sh,
# pi_ais_decode.py, refresh.sh are all untouched).
#
# IDEMPOTENT CURSOR (per stream): a content fingerprint of the decode product is kept in
#   data/.attest_inband_cursor_<stream>
# If the product is identical to the last signed one, the stream is SKIPPED this tick (no
# duplicate receipt). A genuinely new decode advances the cursor and gets one signed line.
#
# HONESTY (operator standing rules):
#   * HONEST-EMPTY: if a stream's decode product file is absent or empty, NO receipt is
#     signed for that stream (we never sign an empty/placeholder batch).
#   * FAILED-CHECK NEVER DROPPED: a frame whose BCS/CRC failed is STILL signed, with the
#     honesty bit (bcs_ok / crc_ok) = 0. The bit is parsed from the decode product
#     ("BCS_OK 1" / "CRC_OK=1") and FAILS CLOSED to 0 when absent — never a fabricated pass.
#   * BEACON: the live pulse is fetched by scripts/fetch_beacon_pulse.sh (sourced). On any
#     fetch failure it stages pulse_id=PENDING_beacon_unreachable; the receipt is STILL
#     signed+chained (the hash-chain is the local clock). NEVER pulse_id=0 / wall-clock.
#   * geo stays PENDING_needs_GPS_PPS (the Rail signers own that; this driver never touches it).
#
# Single-flight: an flock guards against overlapping ticks racing the shared ledgers.
#
# macOS / bash 3.2 only. set -u (NOT set -e: a single stream's failure must not abort the
# other; every failure path is guarded explicitly and falls through to the next stream).

set -u

GD=/Users/ledaticempire/projects/ledaticground
PY=/opt/homebrew/bin/python3.11
RAILRUN="$GD/scripts/railrun.sh"
FETCH="$GD/scripts/fetch_beacon_pulse.sh"

log() { printf '%s attest_inband: %s\n' "$(date -u +%H:%M:%SZ 2>/dev/null || echo '??:??:??Z')" "$*"; }

# ---------- single-flight ----------
exec 8>"/tmp/attest_inband_rollup.lock"
if command -v flock >/dev/null 2>&1; then
  flock -n 8 || { log "another roll-up holds the lock; exit"; exit 0; }
fi

# ---------- 1. fetch the live beacon pulse ONCE (shared by both streams) ----------
# Sourcing stages /tmp/lg_pulse_id.txt + /tmp/lg_pulse_hex.txt (honest PENDING on failure).
if [ -f "$FETCH" ]; then
  # shellcheck disable=SC1090
  . "$FETCH"
else
  log "WARN fetch_beacon_pulse.sh missing; staging honest PENDING pulse"
  printf '%s\n' "PENDING_beacon_unreachable" > /tmp/lg_pulse_id.txt 2>/dev/null
  printf '%s\n' "PENDING_beacon_unreachable" > /tmp/lg_pulse_hex.txt 2>/dev/null
fi
PID_NOW="$(sed -n '1p' /tmp/lg_pulse_id.txt 2>/dev/null || echo PENDING_beacon_unreachable)"
log "beacon pulse_id=$PID_NOW"

# ---------- helpers ----------

# tail chain_hash of a ledger (or GENESIS if absent/empty) -> stdout
ledger_prev() {
  local ledger="$1"
  if [ ! -s "$ledger" ]; then printf '%s' "GENESIS"; return 0; fi
  local ph
  ph="$("$PY" - "$ledger" <<'PYEOF' 2>/dev/null
import json,sys
try:
    lines=[l for l in open(sys.argv[1]) if l.strip()]
    print(json.loads(lines[-1])["chain_hash"] if lines else "GENESIS")
except Exception:
    print("GENESIS")
PYEOF
)"
  case "$ph" in ''|*[!0-9a-fA-F]*) printf '%s' "GENESIS" ;; *) printf '%s' "$ph" ;; esac
}

# content fingerprint of a product file (sha256 of its bytes) -> stdout
product_fp() {
  local f="$1"
  if [ ! -s "$f" ]; then printf '%s' ""; return 0; fi
  shasum -a 256 "$f" 2>/dev/null | awk '{print $1}'
}

# parse a 0/1 honesty bit out of a decode product, FAIL CLOSED to 0.
#   acars: a line "BCS_OK 1" (or "BCS_OK=1");  orbcomm: "CRC_OK=1" (or "CRC_OK 1")
parse_bit() {
  local f="$1" key="$2"
  if [ ! -s "$f" ]; then printf '0'; return 0; fi
  # grep the key (case-insensitive: product writes BCS_OK/CRC_OK uppercase, contract field
  # is lowercase bcs_ok/crc_ok), take the last occurrence, extract a trailing 0/1 token.
  # Fail closed to 0 on any miss.
  local v
  v="$(grep -iE "${key}[ =]" "$f" 2>/dev/null | tail -1 | grep -oE '[01]' | tail -1)"
  case "$v" in 1) printf '1' ;; *) printf '0' ;; esac
}

# batch bounds + count from a decode product. The in-band decode products are single-burst
# artifacts (one decode per file), so n=1 and the bounds are the current scrubbed unix.
# These are provenance BOUNDS, explicitly NOT the attestation clock (that is pulse_id).
batch_bounds() {
  # one decode per product file -> n=1. Use the product file mtime (scrubbed unix) as the
  # provenance bound for both start and end of this single-burst batch.
  local f="$1"
  local ts
  ts="$(stat -f %m "$f" 2>/dev/null || echo PENDING_no_batch)"
  case "$ts" in ''|*[!0-9]*) ts="PENDING_no_batch" ;; esac
  printf '%s' "$ts"
}

# ---------- 2. per-stream roll-up ----------
# args: stream  product_path  signer_rail  ledger_jsonl  bit_key  prefix
rollup_stream() {
  local stream="$1" product="$2" signer="$3" ledger="$4" bitkey="$5" prefix="$6"
  local cursor="$GD/data/.attest_inband_cursor_${stream}"

  # HONEST-EMPTY: no decode product -> no receipt.
  if [ ! -s "$product" ]; then
    log "$stream: no decode product at $product (honest-empty; no receipt)"
    return 0
  fi

  # IDEMPOTENT: skip if this exact product was already signed last tick.
  local fp prev_fp
  fp="$(product_fp "$product")"
  prev_fp=""
  [ -f "$cursor" ] && prev_fp="$(cat "$cursor" 2>/dev/null)"
  if [ -n "$fp" ] && [ "$fp" = "$prev_fp" ]; then
    log "$stream: product unchanged (fp=$fp); idempotent skip"
    return 0
  fi

  # stage prev_sha (chain link), honesty bit (FAIL CLOSED), batch bounds, count.
  local prev bit ts
  prev="$(ledger_prev "$ledger")"
  bit="$(parse_bit "$product" "$bitkey")"
  ts="$(batch_bounds "$product")"
  printf '%s\n' "$prev" > "/tmp/${prefix}_prev_sha.txt"
  printf '%s\n' "$bit"  > "/tmp/${prefix}_${bitkey}.txt"
  printf '%s\n' "$ts"   > "/tmp/${prefix}_batch_start.txt"
  printf '%s\n' "$ts"   > "/tmp/${prefix}_batch_end.txt"
  printf '%s\n' "1"     > "/tmp/${prefix}_batch_n.txt"
  log "$stream: signing (prev=${prev:0:12}.. ${bitkey}=$bit batch_ts=$ts)"

  # sign via the flock-serialized wrapper (dedicated build; never bare /tmp/rail_out).
  if bash "$RAILRUN" "$signer" > "/tmp/${prefix}_attest_run.log" 2>&1; then
    if grep -q "VERIFY .* = 1" "/tmp/${prefix}_attest_run.log" 2>/dev/null; then
      # advance the cursor ONLY after a verified sign+append.
      printf '%s\n' "$fp" > "$cursor"
      log "$stream: SIGNED + appended -> $ledger (cursor advanced)"
    else
      log "$stream: ERR signer ran but VERIFY!=1 — cursor NOT advanced (see /tmp/${prefix}_attest_run.log)"
    fi
  else
    log "$stream: ERR railrun failed — cursor NOT advanced (see /tmp/${prefix}_attest_run.log)"
  fi
  return 0
}

# ACARS  — honesty bit BCS_OK, product = the deframe output, FACT ledger acars_receipts.jsonl
rollup_stream "acars" \
  "/tmp/acars_deframe_out.txt" \
  "$GD/src/acars_attest.rail" \
  "$GD/data/acars_receipts.jsonl" \
  "bcs_ok" "acars"

# ORBCOMM — honesty bit CRC_OK, product = the decode output, FACT ledger orbcomm_receipts.jsonl
# (the signer emits BOTH ORBCOMM_RECEIPT and ORBCOMM_POR_RECEIPT into that ledger).
rollup_stream "orbcomm" \
  "/tmp/orbcomm_decode_out.txt" \
  "$GD/src/orbcomm_attest.rail" \
  "$GD/data/orbcomm_receipts.jsonl" \
  "crc_ok" "orbcomm"

log "roll-up tick complete"
exit 0
