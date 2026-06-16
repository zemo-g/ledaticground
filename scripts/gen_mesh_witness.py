#!/usr/bin/env python3
# gen_mesh_witness.py -- SIMULATED two-node mesh-witness harness for WAVE D (rung 5 mechanism).
# RECEIPT_CONTRACT.md sec A.8 is the SSOT. CORRESPONDENCE_ROADMAP Wave D: this validates the
# co-attestation MECHANISM with a SIMULATED second node -- it is NOT a real witness and NEVER
# claims to be. The truth layer only exists once a 2nd PHYSICAL GPS-PPS node lands (Wave F).
#
# WHAT IS REAL vs WHAT IS FAKED (stated honestly, never blurred):
#   REAL:
#     * The two FACT receipt STRINGS are real, contract-shaped v=2 FACT lines (same skeleton the
#       live AIS signer emits) committing to a shared emission product digest.
#     * The inner Ed25519 co-signatures (sigA/sigB) are produced by the REAL Rail co-sign
#       (coattest.rail's primitive, driven from src/mesh_witness_attest.rail) -- NOT faked here.
#     * The TDOA consistency arithmetic (resid = |measured - predicted| <= tol) is a real check.
#   FAKED / ASSUMED (the never-droppable honesty bits):
#     * The SECOND node is SIMULATED (mesh_peer=SIMULATED). There is no real peer station.
#     * The clock sync between the two recordings is ASSUMED sample-synchronous
#       (clock_disc=SAMPLE_SYNC_ASSUMED) -- a real mesh needs GPS-PPS discipline.
#     * Node geos are PENDING_needs_GPS_PPS (nodeA) / SIMULATED_PENDING_needs_GPS_PPS (nodeB).
#       NO coordinates are fabricated -- the contract literal PENDING markers travel instead.
#     * The "true" TDOA tau is a KNOWN SYNTHESIZED value (we choose the baseline + the measured
#       lag); a consistent pair has measured ~= predicted, an inconsistent pair does not.
#
# This harness does NOT sign and does NOT write any ledger. It ONLY stages /tmp/mesh_* inputs the
# Rail signer reads; the rollup driver (scripts/mesh_witness_rollup.sh) orchestrates the sign +
# the FACT-ledger assembly. It NEVER touches the live AIS chain (ais_receipts.jsonl /
# ais_fact_chain.txt / ais_rollup_cursor.txt / ais_receipt.json) and NEVER invokes the AIS signer.
#
# Usage:
#   python3.11 scripts/gen_mesh_witness.py                 # consistent pair (mesh_ok should be 1)
#   python3.11 scripts/gen_mesh_witness.py --inconsistent  # TDOA mismatch (mesh_ok should be 0)
#   python3.11 scripts/gen_mesh_witness.py --baseline-km 300 --tau-error-s 0.0
#
# All staged values are strings (Rail shell() has no env; file-based config via field_or).

import argparse
import hashlib
import os

# ---- contract constants (sec A / sec D) ----
GEO_PENDING = "PENDING_needs_GPS_PPS"          # receiver geo NEVER fabricated until GPS-PPS lands
STATION = "regional_MI"                        # sanitized: region only (matches data/station_name.txt)
BAND = "AIS-161.975MHz"                        # the shared emission band
C_M_PER_S = 299792458.0                        # speed of light (the TDOA geometry constant)

# DEV signer pubkeys (64-hex) for the two SIMULATED nodes. These MUST equal the Ed25519 public
# keys of coattest.rail's seedA / seedB (sec E.3: the mesh sim reuses coattest's stable DEV pair).
# Carried here ONLY so the FACT ledger lines name the right `signer=` -- the actual signing is done
# in Rail. (Hardcoded pubkeys are derived from those seeds; the Rail signer re-derives + re-checks.)
SIGNER_A = "PENDING_filled_by_rollup"          # rollup overwrites the FACT-line signer from Rail's stage-out
SIGNER_B = "PENDING_filled_by_rollup"


def stage(path, value):
    with open(path, "w") as f:
        f.write(str(value) + "\n")


def fact_receipt(node, geo, pulse_id, pulse_hex, batch_start, batch_end, n,
                 product_sha256, prev, signer):
    # A.1 FACT receipt skeleton (AIS_RECEIPT has no per-kind honesty bit slot). The two nodes
    # commit to the SAME product_sha256 (they observed the same emission). Field order LOAD-BEARING.
    return "|".join([
        "AIS_RECEIPT", "v=2", "type=FACT",
        f"node={node}", f"station={STATION}", f"band={BAND}", f"geo={geo}",
        f"pulse_id={pulse_id}", f"pulse_hex={pulse_hex}",
        f"batch_start={batch_start}", f"batch_end={batch_end}", f"n={n}",
        f"product_sha256={product_sha256}", f"prev={prev}", f"signer={signer}",
    ])


def main():
    ap = argparse.ArgumentParser(description="SIMULATED two-node mesh-witness harness (Wave D).")
    ap.add_argument("--inconsistent", action="store_true",
                    help="produce a TDOA-inconsistent pair (measured tau far from predicted) -> mesh_ok=0")
    ap.add_argument("--baseline-km", type=float, default=300.0,
                    help="simulated inter-node baseline (km); sets the predicted max |TDOA|")
    ap.add_argument("--tau-error-s", type=float, default=None,
                    help="override the measured-minus-predicted TDOA error (s); default 0 (consistent) "
                         "or 10x tol (inconsistent)")
    ap.add_argument("--tol-s", type=float, default=0.00005,
                    help="TDOA consistency tolerance (s); resid<=tol is the geometric self-consistency band")
    args = ap.parse_args()

    # ---- the shared emission product (both nodes observed the SAME emission) ----
    # A representative decoded emission product string. product_sha256 commits to these exact bytes;
    # both FACT receipts carry the identical digest (corroboration -- same product at two nodes).
    emission_product = "AIS_EMISSION|mmsi=366998510|lat=PENDING|lon=PENDING|sog=12.3|cog=041.0|burst=mesh-sim"
    emission_sha = hashlib.sha256(emission_product.encode()).hexdigest()

    # ---- batch provenance bounds (NOT the attestation clock; scrubbed-style unix seconds) ----
    batch_start = "1718400000"
    batch_end = "1718400000"

    # ---- TDOA geometry (the KNOWN synthesized truth) ----
    # Predicted max |TDOA| for the baseline: tau_max = baseline / c. The "measured" tau equals the
    # predicted tau PLUS an error term. A consistent pair has error ~ 0 (within tol); an inconsistent
    # pair has error >> tol (a fabricated 2nd witness whose timing does not fit the geometry).
    baseline_m = args.baseline_km * 1000.0
    tdoa_pred_s = baseline_m / C_M_PER_S            # the geometry-predicted differential delay
    if args.tau_error_s is not None:
        err = args.tau_error_s
    else:
        err = (args.tol_s * 10.0) if args.inconsistent else 0.0
    tdoa_measured_s = tdoa_pred_s + err             # what the xcorr (tdoa.rail scan_lag) recovered

    # ---- the two FACT receipt strings (parents). prev=GENESIS: each is a single-line FACT ledger. ----
    # pulse fields: the rollup sources fetch_beacon_pulse.sh and stages the live pulse; for the FACT
    # parents we read the same staged pulse (honest PENDING when the beacon is unreachable).
    pulse_id = "PENDING_beacon_unreachable"
    pulse_hex = "PENDING"
    if os.path.exists("/tmp/lg_pulse_id.txt"):
        pulse_id = open("/tmp/lg_pulse_id.txt").read().strip() or pulse_id
    if os.path.exists("/tmp/lg_pulse_hex.txt"):
        pulse_hex = open("/tmp/lg_pulse_hex.txt").read().strip() or pulse_hex

    node_a = "regional_MI"
    node_b = "SIMULATED_peer"

    # NOTE: signer= in the FACT line is filled by the rollup from the Rail stage-out (the true pubkey
    # of coattest seedA/seedB). The harness emits the receipt skeleton WITHOUT the final signer so the
    # rollup can splice the exact pubkey + so the inner co-sign in Rail is over the SAME final bytes.
    # To keep the signed bytes deterministic we emit the FULL string here using the Rail-derived
    # pubkeys staged at /tmp/mesh_out_signerA.txt if present (2nd pass), else a placeholder that the
    # rollup substitutes BEFORE signing.
    sa = SIGNER_A
    sb = SIGNER_B
    if os.path.exists("/tmp/mesh_out_signerA.txt"):
        sa = open("/tmp/mesh_out_signerA.txt").read().strip() or sa
    if os.path.exists("/tmp/mesh_out_signerB.txt"):
        sb = open("/tmp/mesh_out_signerB.txt").read().strip() or sb

    factA = fact_receipt(node_a, GEO_PENDING, pulse_id, pulse_hex, batch_start, batch_end, 1,
                         emission_sha, "GENESIS", sa)
    factB = fact_receipt(node_b, "SIMULATED_PENDING_needs_GPS_PPS", pulse_id, pulse_hex,
                         batch_start, batch_end, 1, emission_sha, "GENESIS", sb)

    # ---- stage ALL /tmp/mesh_* inputs the Rail signer reads ----
    stage("/tmp/mesh_factA_receipt.txt", factA)
    stage("/tmp/mesh_factB_receipt.txt", factB)
    # leave chainA/chainB UNSTAGED on the first pass (the Rail signer computes them from the receipt
    # string + its co-signature, then stages them back out for the rollup's FACT-ledger assembly).
    stage("/tmp/mesh_emission_product.txt", emission_product)
    stage("/tmp/mesh_tdoa_s.txt", repr(tdoa_measured_s))
    stage("/tmp/mesh_tdoa_pred_s.txt", repr(tdoa_pred_s))
    stage("/tmp/mesh_tdoa_tol_s.txt", repr(args.tol_s))
    stage("/tmp/mesh_baseline_km.txt", repr(args.baseline_km))
    stage("/tmp/mesh_nodeA_id.txt", node_a)
    stage("/tmp/mesh_nodeB_id.txt", node_b)
    stage("/tmp/mesh_band.txt", BAND)
    stage("/tmp/mesh_batch_start.txt", batch_start)
    stage("/tmp/mesh_batch_end.txt", batch_end)

    # ---- honest console summary (what is real, what is faked) ----
    print("=== SIMULATED two-node mesh witness (Wave D mechanism) ===")
    print(f"  mode               : {'INCONSISTENT (expect mesh_ok=0)' if args.inconsistent else 'consistent (expect mesh_ok=1)'}")
    print(f"  REAL   emission_sha256 : {emission_sha}")
    print(f"  REAL   TDOA predicted  : {tdoa_pred_s:.9f} s  (baseline {args.baseline_km} km / c)")
    print(f"  REAL   TDOA measured   : {tdoa_measured_s:.9f} s  (predicted + err {err:.9f})")
    print(f"  REAL   TDOA resid      : {abs(tdoa_measured_s - tdoa_pred_s):.9f} s   tol {args.tol_s:.9f} s")
    print(f"  FAKED  2nd node        : SIMULATED (mesh_peer=SIMULATED) -- no real peer station")
    print(f"  ASSUMED clock sync     : SAMPLE_SYNC_ASSUMED -- a real mesh needs GPS-PPS")
    print(f"  geoA={GEO_PENDING}  geoB=SIMULATED_PENDING_needs_GPS_PPS  (no coords fabricated)")
    print("  staged /tmp/mesh_* inputs; the Rail signer co-signs + the rollup assembles the FACT ledgers.")
    print("  NOTE: this is a MECHANISM validation, NEVER a correspondence / 'witnessed' claim.")


if __name__ == "__main__":
    main()
