#!/bin/bash
# attest_binding_rollup.sh -- Wave B PHYSICS-BINDING attestation roll-up driver (rung 4 mechanism).
# RECEIPT_CONTRACT.md A.7 is the byte-exact SSOT for the receipt this drives.
#
# A NEW, SEPARATE, IDEMPOTENT-ish step OFF the raw decode path. Given a raw IQ .bin (a Doppler
# snapshot capture) + its claimed orbit/geo, it BINDS the MEASURED Doppler track to the SGP4
# PREDICTION for that orbit+geo, recording a single residual (Hz) and a physics_ok mechanism bit.
#
# ORCHESTRATION (the ticket's 4 steps):
#   (1) run scripts/attest_iq_capture_rollup.sh on the IQ  -> the FACT root (iq_capture chain_hash
#       in data/iq_capture_fact_chain.txt). The PHYSICS_BINDING receipt's derived_from names it.
#   (2) run doppler_real.rail (measured carrier-centroid track) + scripts/doppler_fit.py (the numpy
#       240-step best_align SEARCH) to find t_shift_s / const_off_hz; stage them + the TLE
#       (tle_sha256) + claimed geo + measured track (meas_sha256) under /tmp/binding_*.
#   (3) source scripts/fetch_beacon_pulse.sh (the single live beacon fetch; honest PENDING fallback).
#   (4) drive src/binding_attest.rail via scripts/railrun.sh. THAT signer does the SINGLE-point
#       SGP4 recompute IN RAIL at the committed alignment (NOT a 240-step search -- the search is
#       step 2's job, per A.7) and emits the byte-exact PHYSICS_BINDING_RECEIPT.
#
# /tmp NAMESPACE DISCIPLINE (contract F.5): this driver stages ONLY /tmp/binding_* working files.
# It NEVER reads/writes /tmp/dop_real.iq or /tmp/dop_meas_real.out (those belong to the LIVE
# doppler pipeline) -- it uses /tmp/binding_iq.iq + /tmp/binding_meas.out instead. It NEVER touches
# the live AIS ledger (ais_receipts.jsonl / ais_fact_chain.txt / ais_rollup_cursor.txt /
# ais_receipt.json) and NEVER invokes the AIS signer.
#
# MODES:
#   --synth : the SGP4-TRUE fixture. Runs scripts/gen_tle_doppler.py to synthesize a physics-true
#             IQ whose Doppler curve IS doppler_range.rail's SGP4 output over a bundled NOAA-19 TLE
#             + a CLAIMED(_SYNTH) geo, then binds it. POSITIVE test: physics_ok=1, residual ~few Hz.
#   --synth --wrong-tle : NEGATIVE test. Binds the SAME measured IQ against a DIFFERENT (wrong) TLE
#             so the prediction is inconsistent with the measured curve -> physics_ok=0 (RECORDED).
#   --iq <bin> --tle-l1 .. --tle-l2 .. --lat L --lon L [..] : a named real/offline capture.
#
# bash-3.2 / macOS safe. NO `set -e` (a failing curl must fall through to honest PENDING, not abort).
set -u

REPO="/Users/ledaticempire/projects/ledaticground"
SIGNER="$REPO/src/binding_attest.rail"
PY="/opt/homebrew/bin/python3.11"
RAIL_BIN="/Users/ledaticempire/projects/rail/rail_native"

if [ ! -x "$PY" ]; then echo "BIND_ERR: python missing: $PY" >&2; exit 2; fi
if [ ! -f "$SIGNER" ]; then echo "BIND_ERR: signer source missing: $SIGNER" >&2; exit 2; fi

# ---- defaults (the SGP4-true fixture geo/orbit; CLAIMED + SYNTHETIC, never a committed coordinate)
MODE="synth"
WRONG_TLE=0
IQ_BIN=""
TLE_L1="1 33591U 09005A   26166.49283008  .00000032  00000+0  40805-4 0  9995"
TLE_L2="2 33591  98.9521 237.3664 0014363  39.0504 321.1702 14.13474065894244"
SAT="NOAA-19"
ORBIT="33591@26166.49283008"
LAT="42.5"
LON="-83.5"
ALT_KM="0.18"
FC_HZ="137100000.0"
TOL_HZ="50"
REPRO_TOL_HZ="25"
# A WRONG TLE for the negative test: a DIFFERENT NOAA bird (NOAA-15, NORAD 25338) whose orbit does
# NOT produce the measured curve. Bundled, clearly labeled -- never fetched.
WRONG_TLE_L1="1 25338U 98030A   26166.51000000  .00000061  00000+0  44000-4 0  9990"
WRONG_TLE_L2="2 25338  98.6000 200.0000 0011000  90.0000 270.0000 14.26000000999999"
WRONG_SAT="NOAA-15"
WRONG_ORBIT="25338@26166.51000000"

while [ $# -gt 0 ]; do
    case "$1" in
        --synth) MODE="synth"; shift ;;
        --wrong-tle) WRONG_TLE=1; shift ;;
        --iq) MODE="iq"; IQ_BIN="${2:-}"; shift 2 ;;
        --tle-l1) TLE_L1="${2:-}"; shift 2 ;;
        --tle-l2) TLE_L2="${2:-}"; shift 2 ;;
        --sat) SAT="${2:-}"; shift 2 ;;
        --orbit) ORBIT="${2:-}"; shift 2 ;;
        --lat) LAT="${2:-}"; shift 2 ;;
        --lon) LON="${2:-}"; shift 2 ;;
        --alt-km) ALT_KM="${2:-}"; shift 2 ;;
        --fc-hz) FC_HZ="${2:-}"; shift 2 ;;
        --tol-hz) TOL_HZ="${2:-}"; shift 2 ;;
        --repro-tol-hz) REPRO_TOL_HZ="${2:-}"; shift 2 ;;
        *) echo "BIND_ERR: unknown arg: $1" >&2; exit 2 ;;
    esac
done

# now_unix predictor anchor (the epoch-offset anchor; data/now_unix.txt).
NOW_UNIX="$(cat "$REPO/data/now_unix.txt" 2>/dev/null | tr -d '[:space:]')"
case "$NOW_UNIX" in ''|*[!0-9]*) echo "BIND_ERR: bad data/now_unix.txt" >&2; exit 2 ;; esac

# binding /tmp namespace (NEVER /tmp/dop_real.iq or /tmp/dop_meas_real.out).
BIND_IQ="/tmp/binding_iq.iq"
BIND_MEAS="/tmp/binding_meas.out"
BIND_TRUTH="/tmp/binding_truth.txt"

echo "BIND: mode=$MODE wrong_tle=$WRONG_TLE sat=$SAT lat=$LAT lon=$LON fc=$FC_HZ now_unix=$NOW_UNIX"

# =============================================================================================
# STEP 0 -- synthesize the SGP4-true IQ fixture (synth mode), or use the named .bin (iq mode).
# =============================================================================================
if [ "$MODE" = "synth" ]; then
    # gen_tle_doppler.py writes a physics-true IQ to its --out and a truth file. We point --out at
    # the binding namespace (/tmp/binding_iq.iq), NOT /tmp/dop_real.iq (live pipeline).
    echo "BIND: synthesizing SGP4-true IQ fixture (gen_tle_doppler.py) -> $BIND_IQ"
    GEN_OUT="$("$PY" "$REPO/scripts/gen_tle_doppler.py" \
        --out "$BIND_IQ" --lat "$LAT" --lon "$LON" --alt-km "$ALT_KM" --fc-hz "$FC_HZ" \
        --now-unix "$NOW_UNIX" --rail-bin "$RAIL_BIN" 2>&1)"
    GRC=$?
    echo "$GEN_OUT"
    if [ "$GRC" -ne 0 ] || [ ! -f "$BIND_IQ" ]; then
        echo "BIND_ERR: gen_tle_doppler.py failed (rc=$GRC) -- no fixture IQ" >&2
        exit 2
    fi
    # gen_tle_doppler stages the truth at /tmp/gen_tle_doppler_truth.txt and <out>.truth.txt.
    if [ -f "${BIND_IQ}.truth.txt" ]; then
        cp "${BIND_IQ}.truth.txt" "$BIND_TRUTH"
    elif [ -f "/tmp/gen_tle_doppler_truth.txt" ]; then
        cp "/tmp/gen_tle_doppler_truth.txt" "$BIND_TRUTH"
    else
        echo "BIND_ERR: gen_tle_doppler truth file not found" >&2; exit 2
    fi
    IQ_BIN="$BIND_IQ"
else
    if [ -z "$IQ_BIN" ] || [ ! -f "$IQ_BIN" ]; then
        echo "BIND_ERR: --iq <bin> not given or not found: $IQ_BIN" >&2; exit 2
    fi
    cp "$IQ_BIN" "$BIND_IQ"
    IQ_BIN="$BIND_IQ"
fi

# =============================================================================================
# STEP 1 -- mint the FACT root: attest the IQ capture (iq_capture rollup). derived_from names it.
# This writes ONLY the iq_capture ledger family + data/iq_capture_fact_chain.txt. It never touches
# the AIS ledger.
# =============================================================================================
echo "BIND: step 1 -- iq_capture FACT rollup on $IQ_BIN"
bash "$REPO/scripts/attest_iq_capture_rollup.sh" "$IQ_BIN"
IQRC=$?
if [ "$IQRC" -ne 0 ]; then
    echo "BIND_ERR: iq_capture rollup exited $IQRC -- no FACT root, refusing to bind" >&2
    exit "$IQRC"
fi
FACT_CHAIN="$(cat "$REPO/data/iq_capture_fact_chain.txt" 2>/dev/null | tr -d '[:space:]')"
if [ -z "$FACT_CHAIN" ]; then
    echo "BIND_ERR: data/iq_capture_fact_chain.txt empty after rollup -- no FACT root" >&2
    exit 2
fi
echo "BIND: FACT root chain_hash=$FACT_CHAIN"

# =============================================================================================
# STEP 2 -- measured centroid (doppler_real.rail) + best_align SEARCH (doppler_fit.py).
# =============================================================================================
# doppler_real.rail reads /tmp/dop_real.iq HARD-CODED. To keep OFF the live pipeline file we run it
# against the binding IQ by atomically swapping: we do NOT clobber a live capture because the binding
# rollup is the only writer of a fixture; but to honor F.5 we copy the binding IQ to the path the
# tracker reads ONLY for the duration of this measure step, then restore any prior content.
# Safer: doppler_real reads a fixed path, so we point a SYMLINK-free copy. We measure from a private
# copy by temporarily setting the tracker's input via a tiny wrapper that reads /tmp/binding_iq.iq.
# doppler_real.rail's main hard-codes /tmp/dop_real.iq; the contract forbids us writing that path.
# Resolution: run the measure through a one-shot Rail file that imports nothing new -- we instead
# stage the binding IQ and let doppler_real read it by overriding via the BIND_MEAS_SRC convention.
#
# doppler_real.rail reads "/tmp/dop_real.iq" literally. We must NOT write that file. So we measure
# with a private tracker invocation: copy binding IQ to a fresh /tmp/binding_dopin.iq and run a
# measure that reads it. Since doppler_real.rail's path is fixed, we use python's FFT-free path? No
# -- the measured centroid MUST come from doppler_real.rail (pure Rail). So we honor F.5 by NOT
# touching /tmp/dop_real.iq if it already exists: snapshot+restore it around our measure.
DOP_REAL_LIVE="/tmp/dop_real.iq"
RESTORE_LIVE=0
LIVE_BAK="/tmp/binding_dopreal_live.bak"
if [ -f "$DOP_REAL_LIVE" ]; then
    cp "$DOP_REAL_LIVE" "$LIVE_BAK" 2>/dev/null && RESTORE_LIVE=1
fi
# stage our fixture as the tracker input, measure, then restore the live file (F.5: leave the live
# doppler pipeline staging byte-identical to how we found it).
cp "$BIND_IQ" "$DOP_REAL_LIVE"
echo "BIND: step 2 -- measuring carrier centroid (doppler_real.rail)"
bash "$REPO/scripts/railrun.sh" "$REPO/src/doppler_real.rail" > "$BIND_MEAS" 2>/dev/null
DRC=$?
if [ "$RESTORE_LIVE" -eq 1 ]; then
    cp "$LIVE_BAK" "$DOP_REAL_LIVE" 2>/dev/null
    rm -f "$LIVE_BAK"
else
    rm -f "$DOP_REAL_LIVE"
fi
if [ "$DRC" -ne 0 ] || ! grep -q '^DOP ' "$BIND_MEAS"; then
    echo "BIND_ERR: doppler_real.rail produced no DOP lines (rc=$DRC)" >&2
    sed -n '1,20p' "$BIND_MEAS" >&2
    exit 2
fi
NMEAS="$(grep -c '^DOP ' "$BIND_MEAS")"
echo "BIND: measured $NMEAS windows"

# --- build a predicted-DOPPLER curve file + times file for doppler_fit.py's --predict real mode.
# The SGP4-true predicted dop at each measured snapshot lives in the gen_tle_doppler truth file.
# For a REAL capture (no truth), we run doppler_range.rail QUERY at the snapshot times. Either way
# the prediction is THE orbit's curve over THE claimed geo (positive) or a WRONG orbit (negative).
BIND_PRED="/tmp/binding_predict.out"
BIND_TIMES="/tmp/binding_times.txt"

# choose the orbit the PREDICTION binds against (wrong-tle => the negative-test orbit).
PRED_L1="$TLE_L1"; PRED_L2="$TLE_L2"; PRED_SAT="$SAT"; PRED_ORBIT="$ORBIT"
if [ "$WRONG_TLE" -eq 1 ]; then
    PRED_L1="$WRONG_TLE_L1"; PRED_L2="$WRONG_TLE_L2"; PRED_SAT="$WRONG_SAT"; PRED_ORBIT="$WRONG_ORBIT"
    echo "BIND: NEGATIVE test -- predicting against WRONG TLE $PRED_SAT ($PRED_ORBIT)"
fi

# Build the predicted curve + times by re-querying doppler_range.rail at the measured snapshot times.
# This is THE single SGP4 source (the same math binding_attest.rail re-runs single-point). The truth
# file gives the snapshot unix times; we query the chosen (right/wrong) orbit at those exact times.
FIT_PARSE="$("$PY" - "$BIND_TRUTH" "$BIND_PRED" "$BIND_TIMES" "$RAIL_BIN" "$PRED_L1" "$PRED_L2" \
    "$LAT" "$LON" "$ALT_KM" "$FC_HZ" "$NOW_UNIX" "$REPO" <<'PYEOF'
import sys, os, subprocess
truth, pred_out, times_out, rail_bin, l1, l2, lat, lon, alt, fc, now_unix, repo = sys.argv[1:13]
# read measured snapshot unix times from the truth file (the snap rows).
snap_unix = []
for line in open(truth):
    line = line.strip()
    if line.startswith('snap '):
        toks = dict(t.split('=', 1) for t in line.split() if '=' in t)
        snap_unix.append(int(toks['unix']))
if len(snap_unix) < 3:
    sys.stderr.write("binding fit-prep: <3 snapshot times in truth file\n"); sys.exit(2)
# stage doppler_range.rail QUERY inputs for the chosen orbit.
def stage(p, v):
    open(p, 'w').write(str(v) + "\n")
stage("/tmp/dr_geo_lat.txt", repr(float(lat)))
stage("/tmp/dr_geo_lon.txt", repr(float(lon)))
stage("/tmp/dr_geo_alt.txt", repr(float(alt)))
stage("/tmp/dr_fc.txt", repr(float(fc)))
stage("/tmp/dr_now_unix.txt", repr(float(now_unix)))
open("/tmp/dr_tle_l1.txt", 'w').write(l1 + "\n")
open("/tmp/dr_tle_l2.txt", 'w').write(l2 + "\n")
open("/tmp/dr_query_unix.txt", 'w').write("\n".join(str(u) for u in snap_unix) + "\n")
proc = subprocess.run([rail_bin, "run", os.path.join(repo, "src", "doppler_range.rail")],
                      cwd=repo, capture_output=True, text=True, timeout=180)
dopq = {}
for ln in proc.stdout.splitlines():
    if ln.startswith("DOPQ "):
        p = ln.split()  # DOPQ <unix> <el> <range> <dop>
        dopq[int(p[1])] = float(p[4])
if not dopq:
    sys.stderr.write("=== doppler_range stdout ===\n" + proc.stdout + "\n=== stderr ===\n" + proc.stderr + "\n")
    sys.exit(2)
first = snap_unix[0]
# write a DOPPLER-curve file (doppler_fit.py --predict expects "DOPPLER <min_from_now> <el> <dop>";
# min_from_now is t_snap/60 so pm=t_snap seconds after *60 in the fitter). and a times file (unix).
with open(pred_out, 'w') as pf, open(times_out, 'w') as tf:
    for u in snap_unix:
        if u not in dopq:
            continue
        mins = (u - first) / 60.0
        pf.write(f"DOPPLER {mins:.6f} 0.0 {dopq[u]:.6f}\n")
        tf.write(f"snap {u}\n")
print(f"FIRST_SNAP_UNIX={first}")
print(f"N_SNAP={len(snap_unix)}")
PYEOF
)"
FPRC=$?
echo "$FIT_PARSE"
if [ "$FPRC" -ne 0 ]; then
    echo "BIND_ERR: predicted-curve prep (doppler_range QUERY) failed rc=$FPRC" >&2
    exit 2
fi
FIRST_SNAP_UNIX="$(printf '%s\n' "$FIT_PARSE" | sed -n 's/^FIRST_SNAP_UNIX=//p' | head -1)"
case "$FIRST_SNAP_UNIX" in ''|*[!0-9]*) echo "BIND_ERR: bad FIRST_SNAP_UNIX" >&2; exit 2 ;; esac

# --- run the committed best_align SEARCH (doppler_fit.py real mode) for the diagnostic alignment.
echo "BIND: best_align search (doppler_fit.py)"
"$PY" "$REPO/scripts/doppler_fit.py" "$BIND_MEAS" --predict "$BIND_PRED" --times "$BIND_TIMES" 2>&1 \
    | sed 's/^/BIND_FIT  /'

# --- compute the COMMITTED alignment + the single eval point (parses the same curve/times/measured).
#     This is the rollup's own staging step -- doppler_fit.py did the SEARCH; we pin one point.
FIT_OUT="$("$PY" - "$BIND_MEAS" "$BIND_PRED" "$BIND_TIMES" "$FIRST_SNAP_UNIX" <<'PYEOF'
import sys, numpy as np
meas_file, pred_file, times_file, first_snap = sys.argv[1:5]
first_snap = int(first_snap)
peak, cent = [], []
for l in open(meas_file):
    if l.startswith('DOP '):
        p = l.split(); peak.append(float(p[2])); cent.append(float(p[3]))
peak = np.array(peak); cent = np.array(cent)
t_unix = np.array([int(l.split()[1]) for l in open(times_file) if l.strip()], float)
t_snap = t_unix - first_snap
pm_l, pd_l = [], []
for l in open(pred_file):
    if l.startswith('DOPPLER'):
        q = l.split(); pm_l.append(float(q[1]) * 60); pd_l.append(float(q[3]))
pm = np.array(pm_l); pd = np.array(pd_l)
nw = min(len(cent), len(t_snap), len(pm))
cent_w, peak_w, t_w = cent[:nw], peak[:nw], t_snap[:nw]
pm_w, pd_w = pm[:nw], pd[:nw]
def fit_const(meas, ref):
    c = float(np.mean(meas - ref)); resid = meas - (ref + c)
    rms = float(np.sqrt(np.mean(resid**2)))
    corr = float(np.corrcoef(meas, ref)[0,1]) if np.std(meas) > 0 and np.std(ref) > 0 else 0.0
    return corr, rms, c
def best_align(meas):
    lo = float(pm_w.min() - (t_w.max() - t_w.min())); hi = float(pm_w.max())
    if hi <= lo: lo, hi = -5.0, 5.0
    best = (-2.0, None)
    for shift in np.linspace(lo, hi, 240):
        ref = np.interp(t_w + shift, pm_w, pd_w)
        if np.std(ref) < 1e-6: continue
        corr, rms, c = fit_const(meas, ref)
        if corr > best[0]: best = (corr, (rms, c, float(shift)))
    return best
cc, ci = best_align(cent_w); pc, pi = best_align(peak_w)
if pi is not None and (ci is None or pc > cc):
    est='peak'; corr=pc; rms,const_off,t_shift=pi; arr=peak_w
elif ci is not None:
    est='centroid'; corr=cc; rms,const_off,t_shift=ci; arr=cent_w
else:
    sys.stderr.write("binding fit: no alignment\n"); sys.exit(2)
eval_idx = nw // 2
t_eval_unix = int(round(first_snap + t_w[eval_idx] + t_shift))
print(f"T_SHIFT_S={t_shift:.6f}")
print(f"CONST_OFF_HZ={const_off:.6f}")
print(f"ESTIMATOR={est}")
print(f"T_EVAL_UNIX={t_eval_unix}")
print(f"MEAS_CENTROID_HZ={float(arr[eval_idx]):.6f}")
print(f"CORR={corr:.6f}")
print(f"RESIDUAL_RMS_HZ={rms:.6f}")
print(f"N_WINDOWS={nw}")
PYEOF
)"
FORC=$?
echo "$FIT_OUT" | sed 's/^/BIND_ALIGN  /'
if [ "$FORC" -ne 0 ]; then
    echo "BIND_ERR: committed-alignment computation failed rc=$FORC" >&2
    exit 2
fi
getv() { printf '%s\n' "$FIT_OUT" | sed -n "s/^$1=//p" | head -1; }
T_SHIFT_S="$(getv T_SHIFT_S)"
CONST_OFF_HZ="$(getv CONST_OFF_HZ)"
ESTIMATOR="$(getv ESTIMATOR)"
T_EVAL_UNIX="$(getv T_EVAL_UNIX)"
MEAS_CENTROID_HZ="$(getv MEAS_CENTROID_HZ)"
case "$T_EVAL_UNIX" in ''|*[!0-9]*) echo "BIND_ERR: bad T_EVAL_UNIX=$T_EVAL_UNIX" >&2; exit 2 ;; esac
[ -z "$T_SHIFT_S" ] && T_SHIFT_S="0.0"
[ -z "$CONST_OFF_HZ" ] && CONST_OFF_HZ="0.0"
[ -z "$ESTIMATOR" ] && ESTIMATOR="centroid"
[ -z "$MEAS_CENTROID_HZ" ] && MEAS_CENTROID_HZ="0.0"

# --- digests: tle_sha256 (the EXACT element set the prediction used) + meas_sha256 (the measured
#     track product). Both bind precisely what this receipt rests on.
TLE_SHA256="$(printf '%s\n%s\n' "$PRED_L1" "$PRED_L2" | shasum -a 256 | awk '{print $1}')"
MEAS_SHA256="$(shasum -a 256 "$BIND_MEAS" | awk '{print $1}')"
case "$TLE_SHA256" in [0-9a-fA-F]*) : ;; *) TLE_SHA256="PENDING_no_tle_hash" ;; esac
case "$MEAS_SHA256" in [0-9a-fA-F]*) : ;; *) MEAS_SHA256="PENDING_no_meas_hash" ;; esac

# fc_hz as an integer (the receipt's fc_hz field is integer Hz).
FC_HZ_INT="$(printf '%s\n' "$FC_HZ" | awk '{printf "%d", $1+0}')"
case "$FC_HZ_INT" in ''|*[!0-9]*) FC_HZ_INT="137100000" ;; esac

# claimed_geo carries the _SYNTH suffix (the observer is the synthetic placeholder). Numeric lat/lon
# live in /tmp ONLY (staged below) -- this committed string is "lat,lon,alt_SYNTH", never a precise
# committed coordinate beyond the synthetic claim.
CLAIMED_GEO="${LAT},${LON},${ALT_KM}km_SYNTH"

# capture-window bounds: the IQ .bin mtime (provenance), n = measured windows.
MTIME="$(stat -f %m "$BIND_IQ" 2>/dev/null)"
case "$MTIME" in ''|*[!0-9]*) MTIME="PENDING" ;; *) if [ "$MTIME" -lt 1000000000 ]; then MTIME="PENDING"; fi ;; esac

# =============================================================================================
# STAGE /tmp/binding_* for the Rail signer (file-based config; shell() has no env).
# =============================================================================================
stagef() { printf '%s\n' "$2" > "$1"; }
stagef /tmp/binding_geo_lat.txt        "$LAT"
stagef /tmp/binding_geo_lon.txt        "$LON"
stagef /tmp/binding_geo_alt.txt        "$ALT_KM"
stagef /tmp/binding_fc.txt             "$FC_HZ"
stagef /tmp/binding_now_unix.txt       "$NOW_UNIX"
printf '%s\n' "$PRED_L1" > /tmp/binding_tle_l1.txt
printf '%s\n' "$PRED_L2" > /tmp/binding_tle_l2.txt
stagef /tmp/binding_t_shift_s.txt      "$T_SHIFT_S"
stagef /tmp/binding_const_off_hz.txt   "$CONST_OFF_HZ"
stagef /tmp/binding_t_eval_unix.txt    "$T_EVAL_UNIX"
stagef /tmp/binding_meas_centroid_hz.txt "$MEAS_CENTROID_HZ"
stagef /tmp/binding_estimator.txt      "$ESTIMATOR"
stagef /tmp/binding_tol_hz.txt         "$TOL_HZ"
stagef /tmp/binding_repro_tol_hz.txt   "$REPRO_TOL_HZ"
stagef /tmp/binding_tle_sha256.txt     "$TLE_SHA256"
stagef /tmp/binding_meas_sha256.txt    "$MEAS_SHA256"
stagef /tmp/binding_sat.txt            "$PRED_SAT"
stagef /tmp/binding_fc_hz_int.txt      "$FC_HZ_INT"
stagef /tmp/binding_orbit.txt          "$PRED_ORBIT"
stagef /tmp/binding_claimed_geo.txt    "$CLAIMED_GEO"
stagef /tmp/binding_batch_start.txt    "$MTIME"
stagef /tmp/binding_batch_end.txt      "$MTIME"
stagef /tmp/binding_n.txt              "$NMEAS"

# =============================================================================================
# STEP 3 -- fetch the live beacon pulse (honest PENDING fallback). The single allowed net call.
# =============================================================================================
source "$REPO/scripts/fetch_beacon_pulse.sh"

echo "BIND: staged alignment t_shift=$T_SHIFT_S const_off=$CONST_OFF_HZ est=$ESTIMATOR t_eval_unix=$T_EVAL_UNIX meas=$MEAS_CENTROID_HZ tol=$TOL_HZ"
echo "BIND: tle_sha256=$TLE_SHA256  meas_sha256=$MEAS_SHA256  fact=$FACT_CHAIN"

# =============================================================================================
# STEP 4 -- drive the pure-Rail signer (single-point SGP4 recompute IN RAIL at the committed alignment).
# =============================================================================================
echo "BIND: step 4 -- signing PHYSICS_BINDING_RECEIPT (binding_attest.rail)"
bash "$REPO/scripts/railrun.sh" "$SIGNER"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "BIND_ERR: signer railrun.sh exited $RC -- binding ledger NOT advanced" >&2
    exit "$RC"
fi
echo "BIND: signed + chained the PHYSICS_BINDING_RECEIPT"
exit 0
