#!/bin/bash
# mesh_witness_rollup.sh -- WAVE D (rung 5 mechanism) roll-up driver. RECEIPT_CONTRACT.md sec A.8.
# Orchestrates the SIMULATED two-node mesh co-attestation end to end:
#   1. source fetch_beacon_pulse.sh  (the ONE allowed network call; honest PENDING on failure)
#   2. run the SIMULATED harness (scripts/gen_mesh_witness.py) to stage /tmp/mesh_* FACT pair + TDOA
#   3. drive src/mesh_witness_attest.rail via the flock-serialized railrun.sh -- it co-signs each
#      node's FACT string (inner sigA/sigB, REAL coattest co-sign) and signs the whole witness with
#      the MESH DEV seed (outer); it appends data/mesh_witness_receipts.jsonl + legacy single-object
#      + advances data/chain/mesh_witness_prev.txt, and STAGES OUT sigA/sigB/signerA/signerB/chainA/chainB.
#   4. assemble the two FACT ledger lines (data/mesh_factA_receipts.jsonl /
#      data/mesh_factB_receipts.jsonl) from the receipt strings + Rail-staged inner sigs + chains.
#   5. stage /tmp/lg_verify_target.txt + /tmp/lg_verify_facts.txt so verify.rail can walk the mesh
#      ledger resolving BOTH parents against the two FACT ledgers (and re-verifying the inner sigs).
#
# THE TWO-PASS SIGNER (deterministic, no fabrication):
#   The harness needs the Rail-derived node pubkeys to write a FACT receipt whose `signer=` is the
#   true co-signer key. Ed25519 is deterministic, so:
#     PASS 0  run the signer once with placeholder-signer FACT strings -> stages the real pubkeys.
#     (regen) re-run the harness; it picks up /tmp/mesh_out_signerA/B.txt -> final FACT strings.
#     PASS 1  run the signer again -> co-signs the FINAL FACT strings; the committed parent
#             chain_hashes now match the FACT-ledger lines we assemble byte-for-byte.
#   Both passes append a mesh-witness line; PASS 0's line is over the placeholder facts, so we
#   TRUNCATE the mesh ledger + reset the prev chain to GENESIS between the passes (the committed
#   line is ALWAYS PASS 1's, over the final FACT strings). This keeps the ledger honest: one mesh
#   line, derived_from resolving against the FACT lines actually committed.
#
# *** LIVE AIS CHAIN: HANDS OFF. *** This driver writes ONLY the NEW Wave-D ledgers
# (data/mesh_witness_receipts.jsonl, data/mesh_witness_receipt.json, data/chain/mesh_witness_prev.txt,
# data/mesh_factA_receipts.jsonl, data/mesh_factB_receipts.jsonl) and the /tmp/mesh_* + /tmp/lg_verify_*
# staging files. It NEVER reads/writes the live AIS ledger (ais_receipts.jsonl / ais_fact_chain.txt /
# ais_rollup_cursor.txt / ais_receipt.json) and NEVER invokes the AIS signer.
#
# macOS / bash 3.2. set -u (NOT set -e: every failure path is guarded; the beacon fetch must fall
# through to honest PENDING, not abort the driver).

set -u

GD=/Users/ledaticempire/projects/ledaticground
PY=/opt/homebrew/bin/python3.11
RAILRUN="$GD/scripts/railrun.sh"
FETCH="$GD/scripts/fetch_beacon_pulse.sh"
SIGNER="$GD/src/mesh_witness_attest.rail"
HARNESS="$GD/scripts/gen_mesh_witness.py"

MESH_LEDGER="$GD/data/mesh_witness_receipts.jsonl"
MESH_PREV="$GD/data/chain/mesh_witness_prev.txt"
FACTA_LEDGER="$GD/data/mesh_factA_receipts.jsonl"
FACTB_LEDGER="$GD/data/mesh_factB_receipts.jsonl"

# harness args pass through (e.g. --inconsistent / --baseline-km / --tau-error-s).
HARNESS_ARGS="$*"

log() { printf '%s mesh_rollup: %s\n' "$(date -u +%H:%M:%SZ 2>/dev/null || echo '??:??:??Z')" "$*"; }

mkdir -p "$GD/data/chain" 2>/dev/null

# ---------- single-flight ----------
exec 8>"/tmp/mesh_witness_rollup.lock"
if command -v flock >/dev/null 2>&1; then
  flock -n 8 || { log "another mesh roll-up holds the lock; exit"; exit 0; }
fi

# ---------- SIMULATED fixture: reset the mesh family each run ----------
# The simulated harness regenerates a FRESH FACT pair on every run, so a mesh line left from a prior
# run points to now-overwritten FACT parents (orphaned -> verify.rail correctly REJECTS it). Reset to
# exactly ONE clean mesh line per run, over the CURRENT pair. *** A REAL mesh (Wave F, persistent
# independent witnesses with GPS-PPS) would NOT reset *** -- it would append + prev-chain across
# emissions. This reset is honest ONLY because mesh_peer=SIMULATED; remove it when the peer is REAL.
: > "$MESH_LEDGER" 2>/dev/null
printf 'GENESIS\n' > "$MESH_PREV" 2>/dev/null
rm -f "$FACTA_LEDGER" "$FACTB_LEDGER" 2>/dev/null
log "SIMULATED fixture: reset mesh family (1 clean line/run; a REAL mesh would not reset)"

# ---------- 1. fetch the live beacon pulse ONCE (sourced; honest PENDING on failure) ----------
if [ -f "$FETCH" ]; then
  # shellcheck disable=SC1090
  . "$FETCH"
else
  log "WARN fetch_beacon_pulse.sh missing; staging honest PENDING pulse"
  printf '%s\n' "PENDING_beacon_unreachable" > /tmp/lg_pulse_id.txt 2>/dev/null
  printf '%s\n' "PENDING"                     > /tmp/lg_pulse_hex.txt 2>/dev/null
fi
PID_NOW="$(sed -n '1p' /tmp/lg_pulse_id.txt 2>/dev/null || echo PENDING_beacon_unreachable)"
log "beacon pulse_id=$PID_NOW"

# ---------- clear the per-run stage-out so a stale signerA/B can't leak into PASS 0 ----------
rm -f /tmp/mesh_out_signerA.txt /tmp/mesh_out_signerB.txt \
      /tmp/mesh_out_sigA.txt /tmp/mesh_out_sigB.txt \
      /tmp/mesh_out_chainA.txt /tmp/mesh_out_chainB.txt 2>/dev/null

# ---------- record the mesh ledger / prev state so PASS 0's throwaway line can be rolled back ----------
MESH_PREV_BEFORE="GENESIS"
[ -s "$MESH_PREV" ] && MESH_PREV_BEFORE="$(cat "$MESH_PREV" 2>/dev/null)"
MESH_LINES_BEFORE=0
[ -f "$MESH_LEDGER" ] && MESH_LINES_BEFORE="$(wc -l < "$MESH_LEDGER" 2>/dev/null | tr -d ' ')"
MESH_LINES_BEFORE=${MESH_LINES_BEFORE:-0}

# ---------- 2 + PASS 0: stage placeholder-signer facts, run signer to derive the node pubkeys ----------
log "harness PASS 0 (bootstrap node pubkeys) $HARNESS_ARGS"
# shellcheck disable=SC2086
"$PY" "$HARNESS" $HARNESS_ARGS > /tmp/mesh_harness_pass0.log 2>&1 || { log "ERR harness pass0 failed (see /tmp/mesh_harness_pass0.log)"; exit 1; }
if ! bash "$RAILRUN" "$SIGNER" > /tmp/mesh_pass0_run.log 2>&1; then
  log "ERR signer PASS 0 railrun failed (see /tmp/mesh_pass0_run.log)"; exit 1
fi

# roll back PASS 0's throwaway mesh line + prev pointer (the committed line is always PASS 1's).
# Restore the ledger to EXACTLY its pre-PASS-0 line count. (BSD `head -n 0` is ambiguous across
# platforms, so handle the empty case explicitly with a truncate.)
if [ -f "$MESH_LEDGER" ]; then
  MESH_LINES_NOW="$(wc -l < "$MESH_LEDGER" 2>/dev/null | tr -d ' ')"; MESH_LINES_NOW=${MESH_LINES_NOW:-0}
  if [ "$MESH_LINES_NOW" -gt "$MESH_LINES_BEFORE" ]; then
    if [ "$MESH_LINES_BEFORE" -eq 0 ]; then
      : > "$MESH_LEDGER"   # ledger was empty/absent before this run -> truncate the throwaway line
    else
      head -n "$MESH_LINES_BEFORE" "$MESH_LEDGER" > "${MESH_LEDGER}.tmp" 2>/dev/null && mv "${MESH_LEDGER}.tmp" "$MESH_LEDGER"
    fi
  fi
fi
printf '%s\n' "$MESH_PREV_BEFORE" > "$MESH_PREV"   # restore prev so PASS 1 chains from the real predecessor

# ---------- regen: harness picks up the real pubkeys -> FINAL FACT strings ----------
log "harness regen (final FACT strings with Rail-derived pubkeys)"
# shellcheck disable=SC2086
"$PY" "$HARNESS" $HARNESS_ARGS > /tmp/mesh_harness_final.log 2>&1 || { log "ERR harness regen failed"; exit 1; }

# ---------- PASS 1: co-sign the FINAL facts + sign the witness (the COMMITTED line) ----------
log "signer PASS 1 (commit)"
if ! bash "$RAILRUN" "$SIGNER" > /tmp/mesh_pass1_run.log 2>&1; then
  log "ERR signer PASS 1 railrun failed (see /tmp/mesh_pass1_run.log)"; exit 1
fi
cat /tmp/mesh_pass1_run.log

# guard: the committed run must self-verify (own-sig accepted = 1, tamper = 0).
if ! grep -q "own-sig accepted = 1" /tmp/mesh_pass1_run.log; then
  log "ERR PASS 1 did not self-verify (own-sig != 1) -- NOT assembling FACT ledgers"; exit 1
fi

# ---------- 4. assemble the two FACT ledger lines from the FINAL facts + Rail-staged inner sigs ----------
# Each FACT line: {"v":2,"type":"FACT","receipt": "<factX>","sig": "<sigX>","signer": "<signerX>","chain_hash": "<chainX>"}
# SPACE-AFTER-COLON on every string field verify.rail reads (contract B.2). chain_hash is the Rail-
# computed chain_of(factX, sigX), so verify.rail re-derives the identical value.
assemble_fact_line() {
  local fact_file="$1" sig_file="$2" signer_file="$3" chain_file="$4" out_ledger="$5"
  local fact sig signer chain
  fact="$(cat "$fact_file" 2>/dev/null)"
  sig="$(cat "$sig_file" 2>/dev/null)"
  signer="$(cat "$signer_file" 2>/dev/null)"
  chain="$(cat "$chain_file" 2>/dev/null)"
  if [ -z "$fact" ] || [ -z "$sig" ] || [ -z "$signer" ] || [ -z "$chain" ]; then
    log "ERR missing FACT-line component (fact/sig/signer/chain) -- not writing $out_ledger"; return 1
  fi
  # single-line FACT ledger (prev=GENESIS inside the receipt string). Overwrite (idempotent) -- the
  # mesh sim regenerates a fresh pair each run; this is NOT the live AIS chain.
  printf '{"v":2,"type":"FACT","receipt": "%s","sig": "%s","signer": "%s","chain_hash": "%s"}\n' \
    "$fact" "$sig" "$signer" "$chain" > "$out_ledger"
  log "WROTE $out_ledger (chain_hash=${chain:0:12}..)"
  return 0
}

assemble_fact_line /tmp/mesh_factA_receipt.txt /tmp/mesh_out_sigA.txt /tmp/mesh_out_signerA.txt /tmp/mesh_out_chainA.txt "$FACTA_LEDGER" || exit 1
assemble_fact_line /tmp/mesh_factB_receipt.txt /tmp/mesh_out_sigB.txt /tmp/mesh_out_signerB.txt /tmp/mesh_out_chainB.txt "$FACTB_LEDGER" || exit 1

# ---------- 5. stage verify.rail config: walk the mesh ledger, resolve parents against the FACT ledgers ----------
printf '%s\n' "$MESH_LEDGER" > /tmp/lg_verify_target.txt
printf '%s\n%s\n' "$FACTA_LEDGER" "$FACTB_LEDGER" > /tmp/lg_verify_facts.txt
log "staged /tmp/lg_verify_target.txt -> mesh ledger ; /tmp/lg_verify_facts.txt -> 2 FACT ledgers"

# ---------- sanity: the two parent chain_hashes in derived_from must match the FACT ledger chain_hashes ----------
CHAINA="$(cat /tmp/mesh_out_chainA.txt 2>/dev/null)"
CHAINB="$(cat /tmp/mesh_out_chainB.txt 2>/dev/null)"
log "derived_from parents: A=${CHAINA:0:12}.. B=${CHAINB:0:12}.."

# ---------- DESYNC GUARD: the committed mesh line's parents MUST resolve in the FACT ledgers ----------
# verify.rail walks the mesh line and resolves derived_from = factA_chain_hash;factB_chain_hash by
# looking each parent up as a chain_hash in the two FACT ledgers (an unresolvable parent => REJECT).
# The PASS-0/regen/PASS-1 two-pass dance + the per-run FACT-ledger overwrite are exactly where an
# "output present but stale/empty" desync can hide: a FACT ledger left empty, or a committed mesh
# line whose derived_from drifted from the FACT lines actually written, wedges every verify silently.
# Mirror the iq_capture re-runnability fix: do NOT declare the roll-up complete unless the consumed
# outputs are present, non-empty, AND internally consistent. Fail LOUD otherwise (audit 2026-06-16).
if [ -z "$CHAINA" ] || [ -z "$CHAINB" ]; then
  log "ERR mesh parent chain_hash missing (A='${CHAINA}' B='${CHAINB}') -- cannot verify FACT resolution; FAIL LOUD"; exit 1
fi
# (a) both FACT ledgers must be present + non-empty.
if [ ! -s "$FACTA_LEDGER" ] || [ ! -s "$FACTB_LEDGER" ]; then
  log "ERR a FACT ledger is missing/empty (A=$FACTA_LEDGER B=$FACTB_LEDGER) -- verify would orphan the mesh line; FAIL LOUD"; exit 1
fi
# (b) each FACT ledger must actually contain its parent chain_hash (the value verify.rail resolves).
if ! grep -Fq "\"chain_hash\": \"$CHAINA\"" "$FACTA_LEDGER"; then
  log "ERR factA parent $CHAINA NOT present in $FACTA_LEDGER -- derived_from would be unresolvable; FAIL LOUD"; exit 1
fi
if ! grep -Fq "\"chain_hash\": \"$CHAINB\"" "$FACTB_LEDGER"; then
  log "ERR factB parent $CHAINB NOT present in $FACTB_LEDGER -- derived_from would be unresolvable; FAIL LOUD"; exit 1
fi
# (c) the COMMITTED mesh line's derived_from must equal CHAINA;CHAINB (PASS 1 signed the SAME parents
#     the FACT ledgers carry; a drift here = emit-vs-verify disagreement). Read the committed receipt
#     string's pipe-delimited derived_from= field from the one mesh ledger line.
MESH_DFROM="$(sed -n 's/.*|derived_from=\([^|"]*\).*/\1/p' "$MESH_LEDGER" 2>/dev/null | head -1)"
if [ "$MESH_DFROM" != "${CHAINA};${CHAINB}" ]; then
  log "ERR committed mesh derived_from ('$MESH_DFROM') != staged parents ('${CHAINA};${CHAINB}') -- emit/verify would disagree; FAIL LOUD"; exit 1
fi
log "DESYNC GUARD ok: both FACT ledgers present + contain their parents; mesh derived_from matches"

log "mesh roll-up complete -- run verify.rail to cold-check the witness:"
log "  bash scripts/railrun.sh $GD/src/verify.rail"
exit 0
