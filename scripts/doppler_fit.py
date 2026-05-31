#!/usr/bin/env python3
# Fit a measured Doppler track (from doppler_real.rail "DOP w peak centroid" lines)
# against a reference curve and report the proof-of-reception confidence: after
# removing a best-fit constant frequency offset (SDR ppm + carrier error) and, for
# real data, a best-fit time shift (AOS uncertainty), how well does the measured
# Doppler match the orbital prediction?  High corr + low residual RMS = the curve
# could only have come from THIS orbit over THIS station at THIS time.
#
#   synth: doppler_fit.py <meas.out> --synth
#   real:  doppler_fit.py <meas.out> --predict <doppler_predict.out> --times <times.txt>
import sys, numpy as np

meas_file = sys.argv[1]
peak, cent = [], []
for l in open(meas_file):
    if l.startswith('DOP'):
        p = l.split()
        peak.append(float(p[2])); cent.append(float(p[3]))
peak = np.array(peak); cent = np.array(cent)

def fit_const(meas, ref):
    """best constant offset c minimizing |meas - (ref + c)|; return corr, rms, c."""
    c = float(np.mean(meas - ref))
    resid = meas - (ref + c)
    rms = float(np.sqrt(np.mean(resid**2)))
    corr = float(np.corrcoef(meas, ref)[0, 1]) if np.std(meas) > 0 and np.std(ref) > 0 else 0.0
    return corr, rms, c

if '--synth' in sys.argv:
    truth = np.load('/tmp/dopcap_synth/dop_truth.npy')
    nw = min(len(truth), len(cent))
    print(f'windows: meas={len(cent)} truth={len(truth)} -> using {nw}')
    for name, meas in (('peak', peak[:nw]), ('centroid', cent[:nw])):
        corr, rms, c = fit_const(meas, truth[:nw])
        print(f'  {name:9s}: corr={corr:.4f}  residual_rms={rms:7.1f} Hz  const_off={c:7.1f} Hz')
    cc, _, _ = fit_const(cent[:nw], truth[:nw])
    pc, _, _ = fit_const(peak[:nw], truth[:nw])
    best = 'centroid' if cc >= pc else 'peak'
    print(f'==> best estimator: {best}  (centroid {cc:.4f} vs peak {pc:.4f})')
    sys.exit(0 if max(cc, pc) >= 0.95 else 1)

# real: align measured snapshot times to the predicted DOPPLER curve
pred_file = sys.argv[sys.argv.index('--predict') + 1]
times_file = sys.argv[sys.argv.index('--times') + 1]
t_meas = np.array([int(l.split()[1]) for l in open(times_file) if l.strip()], float)
t_meas = t_meas - t_meas[0]                      # seconds from first snapshot
# predicted curve: lines "DOPPLER <min_from_now> <el> <dop_hz>"
pm_l, pd_l = [], []
for l in open(pred_file):
    if l.startswith('DOPPLER'):
        q = l.split(); pm_l.append(float(q[1])*60); pd_l.append(float(q[3]))
pm = np.array(pm_l); pd = np.array(pd_l)         # pm in seconds from predictor "now"
nw = min(len(cent), len(t_meas))
cent_w, peak_w, t_w = cent[:nw], peak[:nw], t_meas[:nw]

def best_align(meas):
    best = (-2, None)
    for shift in np.linspace(pm.min(), pm.max() - (t_w.max() - t_w.min()), 240):
        ref = np.interp(t_w + shift, pm, pd)
        if np.std(ref) < 1e-6: continue
        corr, rms, c = fit_const(meas, ref)
        if corr > best[0]: best = (corr, (rms, c, shift, ref))
    return best

for name, meas in (('centroid', cent_w), ('peak', peak_w)):
    corr, info = best_align(meas)
    if info is None: print(f'  {name}: no overlap'); continue
    rms, c, shift, _ = info
    print(f'  {name:9s}: corr={corr:.4f}  residual_rms={rms:7.1f} Hz  const_off={c:7.1f} Hz  t_shift={shift:6.0f}s')
