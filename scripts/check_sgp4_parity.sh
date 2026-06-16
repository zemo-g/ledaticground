#!/bin/bash
# check_sgp4_parity.sh -- SGP4 TRIPLICATION-DRIFT GUARD (offline, synthetic, no side effects).
#
# WHY THIS EXISTS (audit 2026-06-16): the SGP4-ish orbital math
# (solve_kepler / gmst_rad / compute_elaz / set_observer / set_elements_from_tle /
#  set_now_unix / range_at / doppler_at / doppler_at_unix) is INLINED VERBATIM in THREE files:
#     src/doppler_range.rail     (the canonical primitive + QUERY mode)
#     src/binding_attest.rail    (the emitter's single-point recompute, then signs)
#     src/verify.rail            (the Wave C verifier's single-point re-run)
# Rail has NO local-module import, so the three copies are maintained by hand. If ANY copy
# drifts (a changed constant, a dropped term, a transposed sign) the emitter and the verifier
# compute DIFFERENT Doppler -> the residual the verifier recomputes no longer matches the
# committed residual_hz -> EVERY physics binding silently FAILS its Wave C re-run (or, worse,
# a drifted-but-self-consistent pair passes while disagreeing with the primitive). No guard
# caught this before. This script is that guard.
#
# WHAT IT DOES: drives ALL THREE inlined copies to predict the SAME Doppler (Hz) for ONE fixed
# (TLE, observer geo, carrier fc, eval unix time) and asserts they agree to within a TIGHT Hz
# tolerance. The three are the SAME math, so they should agree to floating-point identity; the
# tolerance only absorbs show_float's decimal round-trip, not real drift.
#
# HOW IT EXTRACTS each copy's prediction WITHOUT side effects:
#   - The SGP4 functions are a clean, self-contained block in each .rail (asin_ .. doppler_at_unix),
#     containing NO main / NO file I/O / NO signing. We slice that block VERBATIM by line range
#     (sed -n) out of the REAL file -- so any drift in the real file flows straight into the slice;
#     a parity check that retyped the math would be blind to exactly the drift it must catch.
#   - We append a tiny uniform harness main (HARNESS below) that builds the 26-slot element array,
#     stages the FIXED fixture, calls doppler_at_unix once, and prints "PARITY_DOP <dop>".
#   - The generated harness is compiled+run via scripts/railrun.sh (the flock-serialized
#     compile+run wrapper). NOTHING signs, NOTHING appends to a ledger, NOTHING reads the live
#     binding/iq_capture/AIS data files. The three real .rail files are NEVER modified.
#
# ANCHOR NOTE: each copy's doppler_at_unix maps eval-unix u to t = t0 + (u - now_unix), where
#   t0 = (now_unix/86400 + 2440587.5 - jd_epoch)*86400, so the now_unix terms CANCEL and t depends
#   only on u and the TLE epoch. verify.rail exploits this by passing now_unix = t_eval_unix. We do
#   the SAME for all three harnesses (set_now_unix e u; doppler_at_unix e u o) so t0 is computed
#   bit-identically across the three -- the SGP4 math is then the ONLY remaining variable.
#
# FIXTURE: the rollup's --synth defaults (NOAA-19 33591 TLE, observer 42.5,-83.5,0.18km, 137.1 MHz),
#   evaluated at a FIXED deterministic unix time. This is the SAME orbit+geo the binding pipeline's
#   gen_tle_doppler.py synthesizes against, so a parity pass here means emit==verify for that fixture.
#   The eval point happens to be below the horizon for this arbitrary time; that is IRRELEVANT -- the
#   check is a MATH IDENTITY across three copies, not a pass detector. The full pipeline (Kepler,
#   GMST, ECI->ECEF->topocentric, range, 1s range-rate -> Doppler) is exercised either way.
#
# HONESTY: this validates that three inlined copies of a SIMPLIFIED SGP4 (two-body + J2 secular,
#   no drag, no short-period) AGREE -- it is NOT a claim the math is "true" / "verified". It closes
#   a DRIFT gap (emit vs verify disagree), nothing more.
#
# THE GATE IS BYTE-IDENTITY, NOT A TOLERANCE: the three copies are the SAME math driven on the SAME
# input, so they must print the BYTE-IDENTICAL show_float string. We compare the raw strings -- a
# tolerance would create a blind spot (a sub-ppm constant drift on the semi-major axis moves the
# Doppler ~0.00005 Hz, under any reasonable Hz tolerance, yet is still real drift the audit warns of).
# A Hz-magnitude diff is computed too, but ONLY as a human-readable diagnostic of how far apart a
# detected drift is; the PASS/FAIL verdict is string equality.
#
# EXIT: 0 iff all three predicted-Doppler strings are byte-identical (PASS). NONZERO + a loud diff
#       (with the Hz magnitude) if ANY pair differs by even one digit.
#
# bash-3.2 / macOS safe. NO `set -e` (we want to print our own diagnostics on a Rail failure).
set -u

REPO="/Users/ledaticempire/projects/ledaticground"
RAILRUN="$REPO/scripts/railrun.sh"

# The three real files + the verbatim line ranges of their SGP4 block (asin_ .. doppler_at_unix).
# These ranges are asserted below (anchor-line check) so a future edit that shifts the block makes
# the guard fail loud rather than slice the wrong text.
DR_FILE="$REPO/src/doppler_range.rail";   DR_LO=44;  DR_HI=251
BA_FILE="$REPO/src/binding_attest.rail";  BA_LO=80;  BA_HI=257
VF_FILE="$REPO/src/verify.rail";          VF_LO=269; VF_HI=442

# FIXED FIXTURE -- the rollup --synth defaults, frozen here for determinism.
FX_LAT="42.5"
FX_LON="-83.5"
FX_ALT="0.18"
FX_FC="137100000.0"
FX_TLE_L1="1 33591U 09005A   26166.49283008  .00000032  00000+0  40805-4 0  9995"
FX_TLE_L2="2 33591  98.9521 237.3664 0014363  39.0504 321.1702 14.13474065894244"
FX_EVAL_UNIX="1781000300"   # eval time = anchor (now_unix := eval, so t0 is identical across copies)

# The verdict is BYTE-IDENTITY of the show_float strings (see header). This value is only a label
# for the human-readable Hz-magnitude diagnostic and plays NO role in PASS/FAIL.
TOL_HZ="0.0 (verdict is byte-identity; this is a diagnostic-only label)"

WORKDIR="$(mktemp -d /tmp/sgp4_parity.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

fail() { echo "SGP4_PARITY: FAIL -- $*" >&2; }

if [ ! -x "$RAILRUN" ] && [ ! -f "$RAILRUN" ]; then
    fail "railrun wrapper missing: $RAILRUN"; exit 2
fi
for f in "$DR_FILE" "$BA_FILE" "$VF_FILE"; do
    if [ ! -f "$f" ]; then fail "source file missing: $f"; exit 2; fi
done

# ---- anchor-line guard: confirm each slice still STARTS at `asin_` and ENDS at `doppler_at_unix`'s
#      body. If a future edit shifts the block, fail loud (we will NOT silently slice the wrong text).
check_anchor() {
    # $1 file  $2 lo  $3 hi
    local f="$1" lo="$2" hi="$3"
    local first last
    first="$(sed -n "${lo}p" "$f")"
    last="$(sed -n "${hi}p" "$f")"
    case "$first" in
        "asin_ x = atan2 x (sqrt (1.0 - x * x))") : ;;
        *) fail "anchor drift in $f: line $lo is not the asin_ definition (got: '$first'). Update *_LO/*_HI."; return 1 ;;
    esac
    case "$last" in
        *"doppler_at e t o") : ;;
        *) fail "anchor drift in $f: line $hi is not doppler_at_unix's body (got: '$last'). Update *_LO/*_HI."; return 1 ;;
    esac
    # the block must actually CONTAIN the load-bearing function we call.
    if ! sed -n "${lo},${hi}p" "$f" | grep -q '^doppler_at_unix e unix_f o ='; then
        fail "block $lo,$hi of $f does not contain doppler_at_unix -- slice is wrong."; return 1
    fi
    return 0
}
check_anchor "$DR_FILE" "$DR_LO" "$DR_HI" || exit 2
check_anchor "$BA_FILE" "$BA_LO" "$BA_HI" || exit 2
check_anchor "$VF_FILE" "$VF_LO" "$VF_HI" || exit 2

# ---- the uniform harness main appended after each verbatim SGP4 slice. It is IDENTICAL for all
#      three so the SGP4 block is the only thing that varies. Stages the fixed fixture from files
#      (Rail shell() has no env), builds e, evaluates doppler_at_unix once, prints PARITY_DOP.
#      NB: the slice already defines all the fns + the `foreign` math intrinsics live in the prelude.
write_harness() {
    # $1 = abs path of the .rail to generate ; $2 = file with the SGP4 slice
    local out="$1" slice="$2"
    {
        # math intrinsics the SGP4 block calls (foreign decls are file-scoped; the slice omits them).
        echo 'foreign sin x -> float'
        echo 'foreign cos x -> float'
        echo 'foreign sqrt x -> float'
        echo 'foreign pow x y -> float'
        echo 'foreign atan2 y x -> float'
        echo 'foreign floor x -> float'
        echo ''
        # the VERBATIM SGP4 block from the real file.
        cat "$slice"
        echo ''
        # uniform harness: read the fixed fixture from /tmp/sgp4p_* files, predict ONE Doppler.
        cat <<'RAILMAIN'
field_or path def =
  let v = str_replace "\n" "" (read_file path)
  if v == "" then def else v

main =
  let lat = parse_float (field_or "/tmp/sgp4p_lat.txt" "0.0")
  let lon = parse_float (field_or "/tmp/sgp4p_lon.txt" "0.0")
  let alt = parse_float (field_or "/tmp/sgp4p_alt.txt" "0.0")
  let fc = parse_float (field_or "/tmp/sgp4p_fc.txt" "137100000.0")
  let u = parse_float (field_or "/tmp/sgp4p_eval_unix.txt" "0.0")
  let l1 = str_replace "\n" "" (read_file "/tmp/sgp4p_tle_l1.txt")
  let l2 = str_replace "\n" "" (read_file "/tmp/sgp4p_tle_l2.txt")
  let pi = atan2 0.0 (0.0 - 1.0)
  let twopi = pi * 2.0
  let d2r = pi / 180.0
  let mu = 398600.4418
  let re = 6378.137
  let j2 = 0.0010826267
  let e = float_arr_new 26 0.0
  let o = float_arr_new 3 0.0
  let _ = set_observer e lat lon alt d2r twopi mu re j2 fc
  -- anchor = eval time (now_unix terms cancel; matches verify.rail's choice so t0 is identical).
  let _ = set_now_unix e u
  let _ = set_elements_from_tle e l1 l2
  let dop = doppler_at_unix e u o
  let _ = print (cat ["PARITY_DOP ", show_float dop])
  0
RAILMAIN
    } > "$out"
}

# ---- stage the fixed fixture (file-based; Rail shell() inherits no env).
printf '%s\n' "$FX_LAT"     > /tmp/sgp4p_lat.txt
printf '%s\n' "$FX_LON"     > /tmp/sgp4p_lon.txt
printf '%s\n' "$FX_ALT"     > /tmp/sgp4p_alt.txt
printf '%s\n' "$FX_FC"      > /tmp/sgp4p_fc.txt
printf '%s\n' "$FX_EVAL_UNIX" > /tmp/sgp4p_eval_unix.txt
printf '%s\n' "$FX_TLE_L1"  > /tmp/sgp4p_tle_l1.txt
printf '%s\n' "$FX_TLE_L2"  > /tmp/sgp4p_tle_l2.txt

# ---- generate + run one harness per copy; capture its PARITY_DOP.
run_copy() {
    # $1 = label  $2 = real file  $3 = lo  $4 = hi  -> echoes the PARITY_DOP float (or "" on failure)
    local label="$1" f="$2" lo="$3" hi="$4"
    local slice="$WORKDIR/${label}_slice.rail"
    local harness="$WORKDIR/${label}_harness.rail"
    sed -n "${lo},${hi}p" "$f" > "$slice"
    write_harness "$harness" "$slice"
    local out
    out="$(bash "$RAILRUN" "$harness" 2>"$WORKDIR/${label}.err")"
    local dop
    dop="$(printf '%s\n' "$out" | sed -n 's/^PARITY_DOP //p' | head -1)"
    if [ -z "$dop" ]; then
        fail "$label ($f) produced no PARITY_DOP line. Rail output follows:"
        printf '%s\n' "$out" | sed 's/^/    [stdout] /' >&2
        sed 's/^/    [stderr] /' "$WORKDIR/${label}.err" >&2
        echo ""   # empty -> caller treats as failure
        return
    fi
    echo "$dop"
}

DR_DOP="$(run_copy doppler_range "$DR_FILE" "$DR_LO" "$DR_HI")"
BA_DOP="$(run_copy binding_attest "$BA_FILE" "$BA_LO" "$BA_HI")"
VF_DOP="$(run_copy verify "$VF_FILE" "$VF_LO" "$VF_HI")"

echo "SGP4_PARITY: fixture = NOAA-19 33591  obs ${FX_LAT},${FX_LON},${FX_ALT}km  fc ${FX_FC}Hz  eval_unix ${FX_EVAL_UNIX}"
echo "SGP4_PARITY: doppler_range.rail   predicted_dop = ${DR_DOP:-<none>} Hz"
echo "SGP4_PARITY: binding_attest.rail  predicted_dop = ${BA_DOP:-<none>} Hz"
echo "SGP4_PARITY: verify.rail          predicted_dop = ${VF_DOP:-<none>} Hz"

if [ -z "$DR_DOP" ] || [ -z "$BA_DOP" ] || [ -z "$VF_DOP" ]; then
    fail "one or more copies failed to produce a Doppler (see above)."
    exit 1
fi

# ---- PRIMARY GATE: byte-identical show_float strings. The three are the same math on the same
#      input, so anything but an exact string match is drift. (String compare in pure bash; no
#      float rounding can hide a 1-digit difference.) The Hz magnitude is a diagnostic only.
DRIFT=0
[ "$DR_DOP" = "$BA_DOP" ] || DRIFT=1
[ "$DR_DOP" = "$VF_DOP" ] || DRIFT=1
[ "$BA_DOP" = "$VF_DOP" ] || DRIFT=1

# ---- diagnostic only: worst pairwise Hz magnitude + which pairs differ (numpy-free float abs-diff).
CMP="$(/usr/bin/python3 - "$DR_DOP" "$BA_DOP" "$VF_DOP" <<'PY'
import sys
dr, ba, vf = (float(x) for x in sys.argv[1:4])
sdr, sba, svf = sys.argv[1:4]
pairs = [("doppler_range", dr, sdr, "binding_attest", ba, sba),
         ("doppler_range", dr, sdr, "verify",         vf, svf),
         ("binding_attest", ba, sba, "verify",        vf, svf)]
worst = 0.0
diffs = []
for na, a, sa, nb, b, sb in pairs:
    if sa != sb:
        d = abs(a - b)
        if d > worst: worst = d
        diffs.append(f"{na} ({sa} Hz) != {nb} ({sb} Hz)  |diff| = {d:.9f} Hz")
print(f"WORST_DIFF_HZ={worst:.9f}")
for line in diffs:
    print("DIFF " + line)
PY
)"
printf '%s\n' "$CMP" | grep -E '^(WORST_DIFF_HZ|DIFF )' | sed 's/^/SGP4_PARITY: /'

if [ "$DRIFT" -ne 0 ]; then
    echo "" >&2
    fail "================================================================"
    fail " SGP4 DRIFT: the three inlined copies DISAGREE on predicted Doppler."
    fail " emit (binding_attest.rail) and verify (verify.rail) will NOT match;"
    fail " every physics binding's Wave C re-run is at risk of silent failure."
    fail " RECONCILE the SGP4 block across doppler_range.rail / binding_attest.rail / verify.rail."
    fail "================================================================"
    printf '%s\n' "$CMP" | grep '^DIFF ' | sed 's/^DIFF /  /' >&2
    exit 1
fi

echo "SGP4_PARITY: PASS -- all three inlined SGP4 copies predict the BYTE-IDENTICAL Doppler (${DR_DOP} Hz)."
exit 0
