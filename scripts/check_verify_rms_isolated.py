#!/usr/bin/env python3
# lg_iso_validate.py -- FULLY /tmp-ISOLATED validation of the verify.rail RMS/physics_ok fix.
#
# Proves the fix (rms_accum + the two new FAIL conditions in verify.rail) without touching ANY
# file under ledaticground/data/. It drives a /tmp-redirected copy of the REAL binding pipeline
# (attest_binding_rollup.sh + binding_attest.rail) so the honest receipt+fixtures are consistent
# BY CONSTRUCTION, then:
#   POSITIVE  : honest NOAA-19 bind  -> verify.rail reproduces the RMS -> "LEDGER VALID".
#   NEGATIVE  : wrong-orbit (NOAA-15) bind whose single-point residual is HONEST (so the OLD
#               single-point check still reproduces), but residual_rms_hz fabricated small +
#               physics_ok flipped to 1, RE-SIGNED with the dev seed (sig VALID). The NEW checks
#               recompute the true (large) RMS and catch it: "physics RMS unreproducible" +
#               "physics_ok FORGED" -> "LEDGER INVALID".  The bonus assertion that
#               "physics residual unreproducible" is ABSENT proves this is a gap the OLD verifier
#               MISSED -- exactly what the fix closes.
#
# data/ INTEGRITY GUARD: shas of the 3 sensitive files are asserted unchanged before+after every
# rollup run; any drift aborts loudly. Writes NOTHING under data/.
import os, re, sys, subprocess, hashlib

REPO   = "/Users/ledaticempire/projects/ledaticground"
RAIL   = "/Users/ledaticempire/projects/rail/rail_native"
PY     = "/opt/homebrew/bin/python3.11"
RAILRUN        = os.path.join(REPO, "scripts", "railrun.sh")
VERIFY_BINDING = os.path.join(REPO, "scripts", "verify_binding.sh")

BINDING_SEED = "b14d14b14d14b14d14b14d14b14d14b14d14b14d14b14d14b14d14b14d140000"

# the cited TLEs (verbatim from attest_binding_rollup.sh defaults).
NOAA19_L1 = "1 33591U 09005A   26166.49283008  .00000032  00000+0  40805-4 0  9995"
NOAA19_L2 = "2 33591  98.9521 237.3664 0014363  39.0504 321.1702 14.13474065894244"
NOAA15_L1 = "1 25338U 98030A   26166.51000000  .00000061  00000+0  44000-4 0  9990"
NOAA15_L2 = "2 25338  98.6000 200.0000 0011000  90.0000 270.0000 14.26000000999999"

MEAS_FILE  = "/tmp/binding_meas.out"
TIMES_FILE = "/tmp/binding_times.txt"
ISO_LEDGER = "/tmp/iso_ledger.jsonl"

SENSITIVE = [
    os.path.join(REPO, "data", "physics_binding_receipts.jsonl"),
    os.path.join(REPO, "data", "binding_receipt.json"),
    os.path.join(REPO, "data", "chain", "binding_prev.txt"),
]


def sh(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    except OSError:
        return "<absent>"


def snapshot():
    return {p: sh(p) for p in SENSITIVE}


BASELINE = snapshot()


def assert_data_unchanged(stage):
    now = snapshot()
    drift = [p for p in SENSITIVE if now[p] != BASELINE[p]]
    if drift:
        print("\n*** ABORT: data/ MUTATED at stage %r ***" % stage)
        for p in drift:
            print("   %s\n     baseline=%s\n     now     =%s" % (p, BASELINE[p], now[p]))
        sys.exit(3)
    print("  [guard] data/ integrity OK (%s)" % stage)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------------------------------------
# Build the /tmp-isolated pipeline copies (3 data/ writes + the prev read redirected to /tmp).
# ---------------------------------------------------------------------------------------------
def build_iso():
    d = REPO + "/data"
    # binding_attest_iso.rail: redirect the 3 data/ WRITE paths (and the prev READ at line 360,
    # same path as the prev write) to /tmp. Station/fact READ paths are left untouched (read-only).
    seds = [
        "s#%s/physics_binding_receipts.jsonl#%s#g" % (d, ISO_LEDGER),
        "s#%s/binding_receipt.json#/tmp/iso_binding_receipt.json#g" % d,
        "s#%s/chain/binding_prev.txt#/tmp/iso_binding_prev.txt#g" % d,
    ]
    cmd = ["sed"]
    for s in seds:
        cmd += ["-e", s]
    cmd += [os.path.join(REPO, "src", "binding_attest.rail")]
    p = run(cmd)
    if p.returncode != 0:
        sys.exit("build_iso: sed binding_attest failed: " + p.stderr)
    open("/tmp/binding_attest_iso.rail", "w").write(p.stdout)
    # sanity: NO write_file/append_line may still target an absolute data/ path (the relative path
    # survives only in comments, which is harmless). Any absolute data/ WRITE = refuse.
    bad = re.findall(r'(?:append_line|write_file)\s+"/Users/[^"]*/data/[^"]*"', p.stdout)
    if bad:
        sys.exit("build_iso: absolute data/ WRITE still present in iso signer: %r -- refusing" % bad)

    # rollup_iso.sh: point SIGNER at the iso signer; skip the iq_capture sub-rollup (fact-chain
    # file already exists, read-only); redirect the reconcile vars to nonexistent /tmp (-> GENESIS,
    # no data/ write).
    roll = open(os.path.join(REPO, "scripts", "attest_binding_rollup.sh")).read()
    roll = roll.replace('SIGNER="$REPO/src/binding_attest.rail"',
                        'SIGNER="/tmp/binding_attest_iso.rail"')
    roll = roll.replace('bash "$REPO/scripts/attest_iq_capture_rollup.sh" "$IQ_BIN"',
                        'true  # ISO: skip sub-rollup; read existing fact-chain only')
    roll = roll.replace('BIND_LEDGER="$REPO/data/physics_binding_receipts.jsonl"',
                        'BIND_LEDGER="/tmp/iso_nonexistent_ledger.jsonl"')
    roll = roll.replace('BIND_PREV_FILE="$REPO/data/chain/binding_prev.txt"',
                        'BIND_PREV_FILE="/tmp/iso_nonexistent_prev.txt"')
    # belt-and-suspenders: there must be no remaining "$REPO/data/...physics_binding" WRITE target.
    open("/tmp/rollup_iso.sh", "w").write(roll)
    print("  built /tmp/binding_attest_iso.rail + /tmp/rollup_iso.sh")


def run_rollup(extra_args, label):
    # fresh GENESIS each run: clear the iso prev + ledger so binding_attest_iso starts line[0].
    for f in (ISO_LEDGER, "/tmp/iso_binding_prev.txt", "/tmp/iso_binding_receipt.json"):
        try:
            os.remove(f)
        except OSError:
            pass
    print("\n--- running ISO rollup: %s ---" % label)
    p = run(["bash", "/tmp/rollup_iso.sh"] + extra_args, timeout=600)
    tail = "\n".join((p.stdout + p.stderr).splitlines()[-8:])
    print(tail)
    if p.returncode != 0:
        print(p.stdout[-3000:]); print(p.stderr[-2000:])
        sys.exit("run_rollup(%s): rollup exited %d" % (label, p.returncode))
    if not os.path.exists(ISO_LEDGER):
        sys.exit("run_rollup(%s): no /tmp/iso_ledger.jsonl produced" % label)
    return open(ISO_LEDGER).read().strip()


def receipt_of(jline):
    m = re.search(r'"receipt": "([^"]*)"', jline)
    if not m:
        sys.exit("no receipt field in: " + jline[:200])
    return m.group(1)


def pipe_field(receipt, key):
    m = re.search(r"\|" + re.escape(key) + r"=([^|]*)", receipt)
    return m.group(1) if m else ""


def resign(receipt):
    """Re-sign a tampered receipt with the dev binding seed -> a genuine VALID Ed25519 sig."""
    resigner = r'''import "stdlib/sha256.rail"
import "stdlib/sha512.rail"
import "stdlib/x25519.rail"
import "stdlib/ed25519.rail"
import "stdlib/ed25519_scalar.rail"
import "stdlib/ed25519_sign.rail"
main =
  let seed = hex_to_bytes "%s"
  let pk = ed25519_pk_from_sk seed
  let pk_hex = bytes_to_hex pk 32
  let receipt = read_file "/tmp/iso_resign_receipt.txt"
  let msg = string_to_bytes receipt
  let mlen = string_length_bytes receipt
  let sig = ed25519_sign seed msg mlen
  let sig_hex = bytes_to_hex sig 64
  let chain_hash = sha256_hex (cat [receipt, "|sig=", sig_hex])
  let _ = write_file "/tmp/iso_resign_sig.txt" sig_hex
  let _ = write_file "/tmp/iso_resign_pk.txt" pk_hex
  let _ = write_file "/tmp/iso_resign_chain.txt" chain_hash
  0
''' % BINDING_SEED
    open("/tmp/iso_resign_receipt.txt", "w").write(receipt)   # NO trailing newline (verify signs exact bytes)
    open("/tmp/iso_resigner.rail", "w").write(resigner)
    p = run(["bash", RAILRUN, "/tmp/iso_resigner.rail"], timeout=180)
    if p.returncode != 0:
        sys.stderr.write(p.stdout + p.stderr)
        sys.exit("resign: railrun failed")
    return (open("/tmp/iso_resign_sig.txt").read().strip(),
            open("/tmp/iso_resign_pk.txt").read().strip(),
            open("/tmp/iso_resign_chain.txt").read().strip())


def write_single_line_ledger(receipt, sig, signer, chain, path):
    open(path, "w").write(
        '{"v":2,"type":"INFERENCE","receipt": "%s","sig": "%s","signer": "%s","chain_hash": "%s"}\n'
        % (receipt, sig, signer, chain))


def stage_tle(l1, l2, path):
    open(path, "w").write(l1 + "\n" + l2 + "\n")


def run_verify(ledger_path, tle_file, times_file=TIMES_FILE):
    p = run(["bash", VERIFY_BINDING, "--ledger", ledger_path, "--line", "1",
             "--tle-file", tle_file, "--meas-file", MEAS_FILE, "--times-file", times_file],
            timeout=300)
    return p.stdout + p.stderr


def main():
    fails = []
    print("=" * 80)
    print("ISOLATED verify.rail RMS/physics_ok-fix validation  (writes nothing under data/)")
    print("=" * 80)
    assert_data_unchanged("baseline")
    build_iso()

    # ============================ POSITIVE: honest NOAA-19 ============================
    print("\n" + "=" * 80)
    print("POSITIVE: honest NOAA-19 bind (physics_ok=1) -> verify reproduces RMS -> LEDGER VALID")
    print("=" * 80)
    jpos = run_rollup(["--synth"], "synth / NOAA-19 honest")
    assert_data_unchanged("after positive rollup")
    rpos = receipt_of(jpos)
    print("  committed: physics_ok=%s residual_hz=%s residual_rms_hz=%s rms_tol_hz=%s"
          % (pipe_field(rpos, "physics_ok"), pipe_field(rpos, "residual_hz"),
             pipe_field(rpos, "residual_rms_hz"), pipe_field(rpos, "rms_tol_hz")))
    tle_pos = "/tmp/iso_tle_pos.txt"; stage_tle(NOAA19_L1, NOAA19_L2, tle_pos)
    out_pos = run_verify(ISO_LEDGER, tle_pos)        # iso_ledger is the honest line, sig already valid
    print(out_pos)
    pos_times_bound = "times_ok=1" in out_pos        # the new times_sha256 binding is active + matches
    if "==> LEDGER VALID" in out_pos and "unreproducible" not in out_pos and "FORGED" not in out_pos and pos_times_bound:
        print("POSITIVE: PASS (times_ok=1 -> time axis bound + reproduced)\n")
    else:
        fails.append("POSITIVE did not reach a clean LEDGER VALID with times_ok=1 (times_bound=%s)" % pos_times_bound)
        print("POSITIVE: FAIL\n")

    # ============================ NEGATIVE: wrong-orbit RMS/physics_ok forgery ============
    print("=" * 80)
    print("NEGATIVE: wrong-orbit (NOAA-15) bind. Leave residual_hz HONEST (old single-point check")
    print("          still reproduces); fabricate residual_rms_hz->2 + physics_ok->1; RE-SIGN.")
    print("          Expect NEW catches: 'physics RMS unreproducible' + 'physics_ok FORGED' -> INVALID,")
    print("          and 'physics residual unreproducible' ABSENT (the gap the OLD verifier missed).")
    print("=" * 80)
    jneg = run_rollup(["--synth", "--wrong-tle"], "synth --wrong-tle / NOAA-15")
    assert_data_unchanged("after negative rollup")
    rneg = receipt_of(jneg)
    true_resid = pipe_field(rneg, "residual_hz")
    true_rms   = pipe_field(rneg, "residual_rms_hz")
    true_pok   = pipe_field(rneg, "physics_ok")
    print("  TRUE (wrong-orbit honest): residual_hz=%s residual_rms_hz=%s physics_ok=%s rms_tol_hz=%s"
          % (true_resid, true_rms, true_pok, pipe_field(rneg, "rms_tol_hz")))
    fab = rneg
    fab = re.sub(r"\|residual_rms_hz=[^|]*", "|residual_rms_hz=2", fab)   # THE LIE: tiny RMS
    fab = re.sub(r"\|physics_ok=[^|]*", "|physics_ok=1", fab)            # claim a clean bind
    # residual_hz left HONEST so the OLD single-point reproduce-check passes -> only the NEW checks fire.
    print("  FORGED: residual_rms_hz=2 physics_ok=1 (residual_hz=%s kept honest)" % true_resid)
    sig, signer, chain = resign(fab)
    neg_ledger = "/tmp/iso_negative.jsonl"
    write_single_line_ledger(fab, sig, signer, chain, neg_ledger)
    tle_neg = "/tmp/iso_tle_neg.txt"; stage_tle(NOAA15_L1, NOAA15_L2, tle_neg)
    out_neg = run_verify(neg_ledger, tle_neg)
    print(out_neg)
    sig_valid       = ("sig=1" in out_neg)
    rms_caught      = "physics RMS unreproducible" in out_neg
    ledger_invalid  = "==> LEDGER INVALID" in out_neg
    # GAP ISOLATION (rigorous): the OLD single-point check (verify.rail line ~655) reproduces iff its
    # diagnostic shows a small drift. The generic binding-level summary (line ~748) shares the
    # "physics residual unreproducible" substring, so parse the single-point DRIFT directly instead.
    m = re.search(r"recomputed_residual=(\d+) Hz\s+committed residual_hz=(\d+) Hz\s+drift=(\d+)", out_neg)
    single_drift = int(m.group(3)) if m else None
    single_point_reproduced = single_drift is not None and single_drift <= 25
    print("  signals: sig_valid=%s rms_caught=%s invalid=%s single_point_drift=%s (reproduced=%s)"
          % (sig_valid, rms_caught, ledger_invalid, single_drift, single_point_reproduced))
    if rms_caught and ledger_invalid and single_point_reproduced:
        print("NEGATIVE-A: PASS -- single-point residual reproduced EXACTLY (drift=%s<=25, the OLD verifier"
              " would PASS) yet the NEW RMS check rejected the fabricated residual_rms_hz. Gap closed.\n" % single_drift)
    elif rms_caught and ledger_invalid:
        fails.append("NEGATIVE-A caught the forgery but single-point did not reproduce (drift=%s) "
                     "-- gap not isolated" % single_drift)
        print("NEGATIVE-A: WEAK (caught, but old check also fired)\n")
    else:
        fails.append("NEGATIVE-A expected RMS catch + LEDGER INVALID (rms=%s invalid=%s)"
                     % (rms_caught, ledger_invalid))
        print("NEGATIVE-A: FAIL\n")

    # ============== NEGATIVE-B: isolate the physics_ok FORGED path ==============
    # Honest NOAA-19 base (RMS REPRODUCES -> rms_within passes). Tamper rms_tol_hz down to 10 so the
    # reproduced RMS (23) EXCEEDS tol -> the honest verdict is physics_ok=0, but the receipt still
    # claims physics_ok=1. Only physok_match fires: "physics_ok FORGED". residual_hz + residual_rms_hz
    # left honest so neither the single-point nor the RMS-drift check trips -- the physics_ok gate alone.
    print("=" * 80)
    print("NEGATIVE-B: honest NOAA-19, RMS left honest (reproduces); tamper rms_tol_hz->10 so the")
    print("            reproduced RMS exceeds tol -> physics_ok=1 becomes a LIE. Isolate 'physics_ok FORGED'.")
    print("=" * 80)
    # NOAA-19 fixtures + receipt come from the POSITIVE run; the wrong-tle run left a byte-identical
    # measured track (same synth IQ -> same meas_sha256), but re-stage the honest bind to be exact.
    jpos2 = run_rollup(["--synth"], "synth / NOAA-19 honest (re-stage for NEG-B)")
    assert_data_unchanged("after neg-b rollup")
    rpos2 = receipt_of(jpos2)
    fabB = re.sub(r"\|rms_tol_hz=[^|]*", "|rms_tol_hz=10", rpos2)   # physics_ok=1 now inconsistent w/ RMS
    print("  honest: physics_ok=%s residual_rms_hz=%s ; tamper rms_tol_hz %s->10"
          % (pipe_field(rpos2, "physics_ok"), pipe_field(rpos2, "residual_rms_hz"), pipe_field(rpos2, "rms_tol_hz")))
    sigB, signerB, chainB = resign(fabB)
    negB_ledger = "/tmp/iso_negative_b.jsonl"
    write_single_line_ledger(fabB, sigB, signerB, chainB, negB_ledger)
    tleB = "/tmp/iso_tle_posb.txt"; stage_tle(NOAA19_L1, NOAA19_L2, tleB)
    out_negB = run_verify(negB_ledger, tleB)
    print(out_negB)
    physok_caught = "physics_ok FORGED" in out_negB
    rmsB_silent   = "physics RMS unreproducible" not in out_negB
    invalidB      = "==> LEDGER INVALID" in out_negB
    print("  signals: physok_caught=%s rms_check_silent=%s invalid=%s" % (physok_caught, rmsB_silent, invalidB))
    if physok_caught and invalidB and rmsB_silent:
        print("NEGATIVE-B: PASS -- physics_ok forgery caught by the physics_ok gate alone (RMS check silent).\n")
    elif physok_caught and invalidB:
        print("NEGATIVE-B: PASS (physics_ok caught; RMS check also fired)\n")
    else:
        fails.append("NEGATIVE-B expected 'physics_ok FORGED' + LEDGER INVALID "
                     "(physok=%s invalid=%s)" % (physok_caught, invalidB))
        print("NEGATIVE-B: FAIL\n")

    # ============== NEGATIVE-C: time-axis binding (Failure E) ==============
    # The honest NOAA-19 receipt commits times_sha256. Hand the verifier a DIFFERENT time axis (one
    # snapshot shoved 7 s) -- the exact attack times_sha256 defends: fit a wrong orbit by choosing the
    # times the RMS is evaluated at. Expect "physics times-axis unreproducible" -> INVALID, and it
    # fires BEFORE the RMS/physok checks (digest gate). ISO_LEDGER still holds the honest NEG-B receipt.
    print("=" * 80)
    print("NEGATIVE-C: honest NOAA-19 receipt + a TAMPERED time axis (1 snapshot shifted 7s) ->")
    print("            expect 'physics times-axis unreproducible' -> INVALID (Failure E closed).")
    print("=" * 80)
    honest_receipt = receipt_of(open(ISO_LEDGER).read().strip())
    cited_times_sha = pipe_field(honest_receipt, "times_sha256")
    tampered, shifted = [], False
    for ln in open(TIMES_FILE).read().splitlines():
        parts = ln.split()
        if not shifted and len(parts) == 2 and parts[0] == "snap" and parts[1].isdigit():
            tampered.append("snap %d" % (int(parts[1]) + 7)); shifted = True
        else:
            tampered.append(ln)
    tampered_times = "/tmp/iso_times_tampered.txt"
    open(tampered_times, "w").write("\n".join(tampered) + "\n")
    print("  honest receipt cites times_sha256=%s... ; verifier handed a 1-snapshot-shifted axis"
          % cited_times_sha[:16])
    out_negC = run_verify(ISO_LEDGER, tleB, times_file=tampered_times)
    print(out_negC)
    times_caught = "physics times-axis unreproducible" in out_negC
    invalidC     = "==> LEDGER INVALID" in out_negC
    was_bound    = cited_times_sha != "" and cited_times_sha != "PENDING_no_times_hash"
    print("  signals: receipt_bound_times=%s times_caught=%s invalid=%s" % (was_bound, times_caught, invalidC))
    if was_bound and times_caught and invalidC:
        print("NEGATIVE-C: PASS -- a swapped time axis is rejected; the RMS recompute can no longer be"
              " fit by choosing times. Failure E closed.\n")
    else:
        fails.append("NEGATIVE-C expected bound-times + 'physics times-axis unreproducible' + INVALID "
                     "(bound=%s times=%s invalid=%s)" % (was_bound, times_caught, invalidC))
        print("NEGATIVE-C: FAIL\n")

    assert_data_unchanged("final")
    print("=" * 80)
    if fails:
        print("RESULT: FAIL")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("RESULT: PASS -- accepts an honest bind (RMS reproduced, time axis bound) and rejects three")
    print("        valid-signature forgeries the single-point verifier would have passed: fabricated RMS,")
    print("        forged physics_ok, and a swapped time axis (Failure E). data/ untouched.")
    sys.exit(0)


if __name__ == "__main__":
    main()
