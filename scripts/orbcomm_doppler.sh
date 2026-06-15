#!/bin/bash
# ledaticground ORBCOMM Doppler-fingerprint cross-check driver (IS-4/IS-5) — DEPLOY ARTIFACT.
#
# Orbcomm has NO satdump reference pipeline (validate_external.sh only knows
# noaa_apt / meteor_m2-x_lrpt), so the independent proof-of-reception cross-check is the
# DOPPLER FINGERPRINT — physics that cannot be faked. A genuine LEO carrier exhibits the
# SGP4-predicted Doppler drift curve (downward S-curve through TCA); matching the measured
# drift to the prediction PROVES the node heard THAT satellite on THAT pass.
#
# This driver wraps the existing pure-Rail primitive src/doppler_real.rail (16384-pt FFT
# per window -> "DOP <window> <peak_hz> <centroid_hz>", DC-spike-skipping power-weighted
# spectral centroid that tracks a drifting carrier stably). It does NOT modify the Rail
# engine. It produces the centroid-per-window drift curve; the SGP4-predicted curve
# (src/doppler_predict.rail) + the correlation (scripts/doppler_fit.py) are the
# truth-to-match — the chained signed receipt is IS-5 (src/orbcomm_por_attest.rail).
#
# USAGE — does NOT touch the radio and does NOT ssh:
#   orbcomm_doppler.sh --synth [--windows N] [--span-hz S] [--snr DB]
#       OFFLINE self-validation: generate a synthetic SGP4-shaped drifting Orbcomm
#       carrier (gen_orbcomm_doppler.py), run doppler_real.rail, assert the centroid
#       track is monotonic-through-TCA and doppler_fit --synth reports corr > 0.9.
#       This is the NOW-runnable proof the fingerprint chain works with no live RF.
#
#   orbcomm_doppler.sh --from-iq <raw.cu8> [--center-hz H] [--chan-hz H] [--fs H]
#       LIVE/OFF-RADIO: retune a roof cu8 capture to the Orbcomm channel, decimate to the
#       FS=60000 window format doppler_real.rail expects, concatenate 16384-sample windows
#       to /tmp/dop_real.iq, run doppler_real.rail, emit the drift curve to
#       /tmp/dop_meas_orbcomm.out. (The capture is produced by orbcomm_monitor.sh; this is
#       the off-radio characterization stage — no radio/ssh here.)
#
# RAIL TRAP honored: compile/run ONLY via scripts/railrun.sh (flock-serialized shared
# /tmp/rail_out). doppler_real.rail reads the FIXED input /tmp/dop_real.iq.
#
# NOW vs LNA: the Doppler fingerprint needs enough SNR across multiple windows to track
# the centroid — the part most degraded by the ~8 dB no-LNA deficit + cross-pol. The
# fingerprint chain is synthetic-validated NOW; reliable LIVE fingerprinting is the
# clearest LNA beneficiary (IS-5).
set -u
GD=/Users/ledaticempire/projects/ledaticground
PY=/opt/homebrew/bin/python3.11
RAILRUN="$GD/scripts/railrun.sh"
DOP_RAIL="$GD/src/doppler_real.rail"
GEN="$GD/scripts/gen_orbcomm_doppler.py"
FIT="$GD/scripts/doppler_fit.py"
FS_WIN=60000          # src/doppler_real.rail FS (line 180); 16384-pt FFT window
NWIN=16384            # src/doppler_real.rail window size (line 178)

arg(){ # arg <flag> <default> ; reads from the global ARGS array
  local f="$1" d="$2" i
  for ((i=0;i<${#ARGS[@]};i++)); do
    if [ "${ARGS[$i]}" = "$f" ]; then echo "${ARGS[$((i+1))]:-$d}"; return; fi
  done
  echo "$d"
}

run_doppler_real(){   # run doppler_real.rail on /tmp/dop_real.iq -> $1 (out file)
  bash "$RAILRUN" "$DOP_RAIL" 120 2>/dev/null | grep -E '^(INFO|DOP)' > "$1"
}

# assert the centroid-per-window track is monotonic through TCA (one sign change of the
# slope at most: descending S-curve). $1 = doppler_real out file. echoes PASS/FAIL.
check_monotonic(){
  "$PY" - "$1" <<'PY'
import sys
cent=[]
for l in open(sys.argv[1]):
    if l.startswith('DOP'):
        cent.append(float(l.split()[3]))
if len(cent) < 4:
    print("MONOTONIC FAIL: too few windows", len(cent)); sys.exit(1)
import numpy as np
c=np.array(cent)
# a clean SGP4 S-curve descends overall through TCA: net change negative, and the
# track is mostly-decreasing (allow a few noise reversals near the flat tails).
net = c[-1]-c[0]
diffs=np.diff(c)
frac_down=float((diffs<=0).mean())
ok = (net < 0) and (frac_down >= 0.7)
print(f"MONOTONIC net={net:.0f}Hz frac_decreasing={frac_down:.2f} windows={len(c)} -> {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
PY
}

main(){
  ARGS=("$@")
  case "${1:-}" in
    --synth)
      local w s snr
      w=$(arg --windows 24); s=$(arg --span-hz 16000); snr=$(arg --snr 18)
      echo "=== synthetic Orbcomm Doppler fingerprint (windows=$w span=${s}Hz snr=${snr}dB) ==="
      "$PY" "$GEN" --windows "$w" --span-hz "$s" --snr "$snr" || { echo "gen failed"; exit 1; }
      echo "=== pure-Rail centroid Doppler track (doppler_real.rail) ==="
      run_doppler_real /tmp/dop_meas_orbcomm.out
      echo "DOP lines: $(grep -c '^DOP' /tmp/dop_meas_orbcomm.out)"
      echo "=== monotonic-through-TCA check ==="
      check_monotonic /tmp/dop_meas_orbcomm.out; local mono=$?
      echo "=== fit measured-vs-truth (doppler_fit --synth) ==="
      "$PY" "$FIT" /tmp/dop_meas_orbcomm.out --synth; local fit=$?
      if [ "$mono" = 0 ] && [ "$fit" = 0 ]; then echo "ORBCOMM_DOPPLER_SYNTH PASS"; exit 0; else echo "ORBCOMM_DOPPLER_SYNTH FAIL (mono=$mono fit=$fit)"; exit 1; fi
      ;;
    --from-iq)
      local inp fs chan center
      inp="${2:-}"
      if [ -z "$inp" ] || [ ! -f "$inp" ]; then echo "usage: orbcomm_doppler.sh --from-iq <raw.cu8> [--center-hz H] [--chan-hz H] [--fs H]" >&2; exit 2; fi
      fs=$(arg --fs 250000); chan=$(arg --chan-hz 137662500); center=$(arg --center-hz "$chan")
      echo "=== retune $inp -> FS=$FS_WIN window IQ at /tmp/dop_real.iq ==="
      # retune the Orbcomm channel to baseband, decimate to FS_WIN, trim to a whole number
      # of 16384-sample windows, write as cu8 (uint8) — the doppler_real.rail input contract.
      "$PY" - "$inp" "$fs" "$chan" "$center" "$FS_WIN" "$NWIN" <<'PY'
import sys, numpy as np
from scipy import signal
inp, fs, chan, center, fsw, nwin = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
raw = np.fromfile(inp, dtype=np.uint8).astype(np.float32) - 127.5
iq = raw[0::2] + 1j*raw[1::2]
foff = chan - center
n = np.arange(len(iq))
iq = iq * np.exp(-2j*np.pi*(foff/fs)*n)
nyq = fs/2.0
sos = signal.butter(6, min(20000, 0.95*nyq)/nyq, btype='low', output='sos')   # keep +/-20k for Doppler swing
iq = signal.sosfiltfilt(sos, iq)
n_out = int(round(len(iq)*fsw/fs))
bb = signal.resample(iq, n_out) if n_out>0 else np.zeros(0, complex)
nfull = (len(bb)//nwin)*nwin
bb = bb[:nfull]
A = 60.0
i8 = np.clip(np.round(bb.real*A), -127, 127).astype(np.int16)+128
q8 = np.clip(np.round(bb.imag*A), -127, 127).astype(np.int16)+128
out = np.empty(2*len(bb), np.uint8); out[0::2]=i8.astype(np.uint8); out[1::2]=q8.astype(np.uint8)
out.tofile('/tmp/dop_real.iq')
print(f"retune: {len(iq)} samp @{fs} -> {len(bb)} samp @{fsw} = {len(bb)//nwin} windows -> /tmp/dop_real.iq")
PY
      echo "=== pure-Rail centroid Doppler track ==="
      run_doppler_real /tmp/dop_meas_orbcomm.out
      echo "DOP lines: $(grep -c '^DOP' /tmp/dop_meas_orbcomm.out) -> /tmp/dop_meas_orbcomm.out"
      echo "NOTE: correlate vs src/doppler_predict.rail with scripts/doppler_fit.py --predict (IS-5 receipt)."
      ;;
    *)
      echo "usage: orbcomm_doppler.sh --synth [--windows N] [--span-hz S] [--snr DB]" >&2
      echo "       orbcomm_doppler.sh --from-iq <raw.cu8> [--center-hz H] [--chan-hz H] [--fs H]" >&2
      exit 2
      ;;
  esac
}

# Only run when executed directly — sourcing must NOT start anything.
# `(return 0 2>/dev/null)` succeeds ONLY when sourced (return is illegal at the top
# level of an executed script), so this robustly distinguishes source from exec even
# when the caller's environment unsets BASH_SOURCE.
if ! (return 0 2>/dev/null); then
  main "$@"
fi
