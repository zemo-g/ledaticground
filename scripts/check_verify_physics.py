#!/usr/bin/env python3
# check_verify_physics.py -- WAVE C accept/reject harness for the physics-running verifier.
# RECEIPT_CONTRACT.md A.7 is the SSOT. This drives src/verify.rail through scripts/verify_binding.sh
# and asserts the two outcomes that make "physicify with teeth" real:
#
#   POSITIVE: a GENUINE binding receipt (physics_ok, honest residual) re-runs through verify.rail's
#             SGP4 single-point recompute and REPRODUCES the committed residual within repro_tol_hz
#             -> "==> LEDGER VALID".
#
#   NEGATIVE (the whole point): take a GENUINE receipt, hand-edit residual_hz to a FABRICATED small
#             value AND flip physics_ok to 1 ("this WAS a clean bind"), then RE-SIGN it with the
#             binding DEV seed (b14d..0000) so the Ed25519 signature is PERFECTLY VALID. verify.rail
#             re-runs the physics, recomputes the TRUE residual from the cited TLE+geo+track, finds it
#             does NOT match the fabricated number (drift > repro_tol_hz), and REJECTS with
#             "physics residual unreproducible" -> "==> LEDGER INVALID". A valid signature over a
#             fabricated residual MUST fail. THE CLAIM stays "the cost of an undetected lie scales
#             with the conspiracy required" -- to pass, the liar would also have to forge a TLE+track
#             whose SGP4 single-point residual equals the fabricated number AND whose sha256 digests
#             match the cited tle_sha256/meas_sha256. Never "proof of truth".
#
# It uses LINE 2 of the committed binding ledger as the negative base BY DEFAULT: that line is the
# wrong-TLE (NOAA-15) bind whose TRUE residual is large (~123 Hz, physics_ok=0). Fabricating it to a
# tiny residual + physics_ok=1 produces a >repro_tol drift the re-run catches. The positive base is
# LINE 1 (NOAA-19, honest residual ~2 Hz, physics_ok=1).
#
# *** LIVE AIS CHAIN: HANDS OFF. *** Writes ONLY /tmp/* fixtures; reads the binding + iq_capture
# ledgers read-only; never touches the AIS ledger or runs the AIS signer. Writes NOTHING under data/.
#
# Usage:  python3 scripts/check_verify_physics.py            (runs POSITIVE + NEGATIVE, exits nonzero on fail)
#         python3 scripts/check_verify_physics.py --keep      (leave /tmp fixtures for inspection)
import os, re, sys, subprocess, tempfile

REPO = "/Users/ledaticempire/projects/ledaticground"
RAIL = "/Users/ledaticempire/projects/rail/rail_native"
RAILRUN = os.path.join(REPO, "scripts", "railrun.sh")
VERIFY_BINDING = os.path.join(REPO, "scripts", "verify_binding.sh")
BIND_LEDGER = os.path.join(REPO, "data", "physics_binding_receipts.jsonl")

# The two cited TLEs (verbatim from attest_binding_rollup.sh defaults). Their sha256 must equal the
# tle_sha256 the receipt cited (line1=NOAA-19, line2=NOAA-15 wrong-tle). Bundled, never fetched.
NOAA19_L1 = "1 33591U 09005A   26166.49283008  .00000032  00000+0  40805-4 0  9995"
NOAA19_L2 = "2 33591  98.9521 237.3664 0014363  39.0504 321.1702 14.13474065894244"
NOAA15_L1 = "1 25338U 98030A   26166.51000000  .00000061  00000+0  44000-4 0  9990"
NOAA15_L2 = "2 25338  98.6000 200.0000 0011000  90.0000 270.0000 14.26000000999999"

BINDING_SEED = "b14d14b14d14b14d14b14d14b14d14b14d14b14d14b14d14b14d14b14d140000"

# the shared measured-track + snapshot-times the binding fixture leaves staged after a rollup run.
MEAS_FILE = "/tmp/binding_meas.out"
TIMES_FILE = "/tmp/binding_times.txt"


def read_binding_line(n):
    """1-based n-th PHYSICS_BINDING_RECEIPT json line of the committed ledger."""
    lines = [l for l in open(BIND_LEDGER) if "PHYSICS_BINDING_RECEIPT" in l]
    if n < 1 or n > len(lines):
        sys.exit(f"check_verify_physics: --line {n} out of range (1..{len(lines)})")
    return lines[n - 1].rstrip("\n")


def receipt_of(jline):
    m = re.search(r'"receipt": "([^"]*)"', jline)
    if not m:
        sys.exit("check_verify_physics: no receipt field in json line")
    return m.group(1)


def pipe_field(receipt, key):
    m = re.search(r"\|" + re.escape(key) + r"=([^|]*)", receipt)
    return m.group(1) if m else ""


def resign(receipt):
    """Sign a (possibly tampered) receipt string with the binding DEV seed via a tiny Rail signer.
    Returns (sig_hex, signer_hex, chain_hash). Deterministic Ed25519 -> a genuine, VALID signature."""
    resigner = """import "stdlib/sha256.rail"
import "stdlib/sha512.rail"
import "stdlib/x25519.rail"
import "stdlib/ed25519.rail"
import "stdlib/ed25519_scalar.rail"
import "stdlib/ed25519_sign.rail"
main =
  let seed = hex_to_bytes \"%s\"
  let pk = ed25519_pk_from_sk seed
  let pk_hex = bytes_to_hex pk 32
  let receipt = read_file \"/tmp/cvp_resign_receipt.txt\"
  let msg = string_to_bytes receipt
  let mlen = string_length_bytes receipt
  let sig = ed25519_sign seed msg mlen
  let sig_hex = bytes_to_hex sig 64
  let chain_hash = sha256_hex (cat [receipt, \"|sig=\", sig_hex])
  let _ = write_file \"/tmp/cvp_resign_sig.txt\" sig_hex
  let _ = write_file \"/tmp/cvp_resign_pk.txt\" pk_hex
  let _ = write_file \"/tmp/cvp_resign_chain.txt\" chain_hash
  0
""" % BINDING_SEED
    # IMPORTANT: read_file keeps a trailing newline, but verify.rail signs/recomputes over the EXACT
    # receipt string with NO trailing newline. Write the receipt with no trailing newline so the
    # signed bytes match what verify.rail's field_val extracts (no newline inside a JSON string).
    open("/tmp/cvp_resign_receipt.txt", "w").write(receipt)
    open("/tmp/cvp_resigner.rail", "w").write(resigner)
    p = subprocess.run(["bash", RAILRUN, "/tmp/cvp_resigner.rail"],
                       capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        sys.stderr.write(p.stdout + p.stderr)
        sys.exit("check_verify_physics: re-signer railrun failed")
    sig = open("/tmp/cvp_resign_sig.txt").read().strip()
    pk = open("/tmp/cvp_resign_pk.txt").read().strip()
    chain = open("/tmp/cvp_resign_chain.txt").read().strip()
    return sig, pk, chain


def write_single_line_ledger(receipt, sig, signer, chain, path):
    # space-after-colon on every string field verify.rail reads (contract B.2).
    line = ('{"v":2,"type":"INFERENCE","receipt": "%s","sig": "%s","signer": "%s","chain_hash": "%s"}\n'
            % (receipt, sig, signer, chain))
    open(path, "w").write(line)


def stage_tle(l1, l2, path):
    # printf '%s\n%s\n' -> the exact bytes the rollup's tle_sha256 commits to.
    open(path, "w").write(l1 + "\n" + l2 + "\n")


def run_verify(ledger_path, tle_file):
    """Run verify_binding.sh against a single-line ledger fixture. Returns the combined stdout."""
    p = subprocess.run(
        ["bash", VERIFY_BINDING, "--ledger", ledger_path, "--line", "1",
         "--tle-file", tle_file, "--meas-file", MEAS_FILE, "--times-file", TIMES_FILE],
        capture_output=True, text=True, timeout=300)
    return p.stdout + p.stderr


def main():
    keep = "--keep" in sys.argv
    if not os.path.exists(MEAS_FILE) or not os.path.exists(TIMES_FILE):
        sys.exit(f"check_verify_physics: staged fixture missing ({MEAS_FILE} / {TIMES_FILE}); "
                 f"run scripts/attest_binding_rollup.sh --synth first to leave the measured track + times.")

    fails = []

    # ---------------- POSITIVE: genuine line 1 (NOAA-19) reproduces its residual ----------------
    print("=" * 78)
    print("POSITIVE: genuine binding (line 1, NOAA-19, physics_ok=1) -> expect LEDGER VALID")
    print("=" * 78)
    j1 = read_binding_line(1)
    r1 = receipt_of(j1)
    # build a GENESIS single-line fixture from the genuine line (re-sign genuine receipt; sig identical).
    sig1, signer1, chain1 = resign(r1)
    pos_ledger = "/tmp/cvp_positive.jsonl"
    write_single_line_ledger(r1, sig1, signer1, chain1, pos_ledger)
    tle1 = "/tmp/cvp_tle_pos.txt"
    stage_tle(NOAA19_L1, NOAA19_L2, tle1)
    out_pos = run_verify(pos_ledger, tle1)
    print(out_pos)
    if "==> LEDGER VALID" in out_pos and "physics residual unreproducible" not in out_pos:
        print("POSITIVE: PASS (genuine binding re-runs + reproduces residual -> VALID)\n")
    else:
        fails.append("POSITIVE did not reach LEDGER VALID")
        print("POSITIVE: FAIL\n")

    # ---------------- NEGATIVE: fabricate residual + flip physics_ok, RE-SIGN (valid sig) -------
    print("=" * 78)
    print("NEGATIVE: line 2 (NOAA-15 wrong-tle, TRUE residual ~123 Hz, physics_ok=0); fabricate")
    print("          residual_hz=2 + physics_ok=1, RE-SIGN with the binding seed (sig VALID) ->")
    print("          expect 'physics residual unreproducible' + LEDGER INVALID")
    print("=" * 78)
    j2 = read_binding_line(2)
    r2 = receipt_of(j2)
    true_resid = pipe_field(r2, "residual_hz")
    true_physok = pipe_field(r2, "physics_ok")
    # THE LIE: claim a tiny residual + a clean bind. Also rewrite prev= to GENESIS so the single-line
    # fixture's structural gates (prev link) pass and the FAIL is unambiguously the PHYSICS re-run.
    fab = r2
    fab = re.sub(r"\|residual_hz=[^|]*", "|residual_hz=2", fab)
    fab = re.sub(r"\|physics_ok=[^|]*", "|physics_ok=1", fab)
    fab = re.sub(r"\|prev=[^|]*", "|prev=GENESIS", fab)
    print(f"  TRUE   residual_hz={true_resid} physics_ok={true_physok}")
    print(f"  FORGED residual_hz=2 physics_ok=1 (and prev=GENESIS for a clean single-line walk)")
    # RE-SIGN the tampered string with the genuine binding seed -> a VALID Ed25519 signature.
    sig2, signer2, chain2 = resign(fab)
    neg_ledger = "/tmp/cvp_negative.jsonl"
    write_single_line_ledger(fab, sig2, signer2, chain2, neg_ledger)
    tle2 = "/tmp/cvp_tle_neg.txt"
    stage_tle(NOAA15_L1, NOAA15_L2, tle2)
    out_neg = run_verify(neg_ledger, tle2)
    print(out_neg)
    sig_was_valid = "sig=1" in out_neg
    physics_failed = "physics residual unreproducible" in out_neg
    ledger_invalid = "==> LEDGER INVALID" in out_neg
    if sig_was_valid and physics_failed and ledger_invalid:
        print("NEGATIVE: PASS (signature VALID, physics re-run REJECTED the fabricated residual -> INVALID)\n")
    else:
        fails.append(f"NEGATIVE expected sig=1 + 'physics residual unreproducible' + LEDGER INVALID "
                     f"(got sig_valid={sig_was_valid} physics_failed={physics_failed} invalid={ledger_invalid})")
        print("NEGATIVE: FAIL\n")

    if not keep:
        for f in ("/tmp/cvp_positive.jsonl", "/tmp/cvp_negative.jsonl", "/tmp/cvp_tle_pos.txt",
                  "/tmp/cvp_tle_neg.txt", "/tmp/cvp_resigner.rail", "/tmp/cvp_resign_receipt.txt",
                  "/tmp/cvp_resign_sig.txt", "/tmp/cvp_resign_pk.txt", "/tmp/cvp_resign_chain.txt"):
            try:
                os.remove(f)
            except OSError:
                pass

    print("=" * 78)
    if fails:
        print("RESULT: FAIL")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("RESULT: PASS -- physics-running verifier accepts honest bindings and rejects a")
    print("        valid-signature/fabricated-residual forgery (physicify with teeth).")
    sys.exit(0)


if __name__ == "__main__":
    main()
