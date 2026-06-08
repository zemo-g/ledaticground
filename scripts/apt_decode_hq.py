#!/usr/bin/env python3.11
# Heavy-duty NOAA APT decoder for marginal/overloaded recordings — does what apt.rail can't:
# AM-demod the 2400 Hz subcarrier (bandpass + Hilbert envelope), resample to the 4160 px/s
# APT word rate, then LOCK the line grid by searching line-period x phase for the strongest
# comb of Sync-A correlations. Emits a PNG + a SYNC_LOCK quality number (the decisive metric:
# a real image has a strong, regular sync comb; noise does not).
#
#   apt_decode_hq.py <in.s16|in.wav> <out.png> [--sr 11025] [--denoise] [--chan A|B|AB]
#
# SYNC_LOCK >> 1 (say >3) => genuine image structure recovered. ~1 => no signal (noise).
import sys, numpy as np
from scipy import signal
from PIL import Image

args = sys.argv
INP = args[1]; OUT = args[2]
def opt(flag, default=None):
    return args[args.index(flag)+1] if flag in args else default
SR = int(opt('--sr', 11025))
DENOISE = '--denoise' in args
CHAN = opt('--chan', 'A')
PIX = 4160                      # APT pixel/word rate (Hz)
LINE = 2080                     # pixels per line (2 lines/s)
SUBC = 2400.0                   # APT subcarrier (Hz)

# ---- load ----
if INP.lower().endswith('.wav'):
    from scipy.io import wavfile
    sr_in, x = wavfile.read(INP); x = x.astype(np.float64)
    if x.ndim > 1: x = x[:,0]
    SR = sr_in
else:
    x = np.fromfile(INP, dtype='<i2').astype(np.float64)
x -= x.mean()
if len(x) < SR*5:
    print(f"SYNC_LOCK 0.00 | too short ({len(x)/SR:.1f}s)"); sys.exit(1)

# ---- AM demod of the 2400 Hz subcarrier ----
# bandpass around the subcarrier, then Hilbert envelope = pixel-intensity stream.
ny = SR/2.0
bp = signal.butter(4, [max(500,SUBC-1500)/ny, min(ny-50,SUBC+1500)/ny], btype='band', output='sos')
xb = signal.sosfiltfilt(bp, x)
env = np.abs(signal.hilbert(xb))

# optional spectral-gate denoise on the envelope (helps marginal SNR)
if DENOISE:
    # subtract a slow noise estimate, clip negatives
    base = signal.medfilt(env, kernel_size=201) if len(env) > 201 else env
    env = np.clip(env - 0.5*np.median(env), 0, None)

# ---- resample envelope to the 4160 px/s word rate ----
n_out = int(round(len(env) * PIX / SR))
px = signal.resample(env, n_out)
px = px - px.min()

# ---- Sync-A template: 7 pulses of 1040 Hz (alternating) at 4160 px/s => 4 px/cycle ----
tmpl = np.tile([-1.0,-1.0,1.0,1.0], 7)            # 28-sample sync comb
corr = signal.correlate(px - px.mean(), tmpl, mode='valid')
corr = np.maximum(corr, 0)                          # only positive (matched) sync hits

# ---- lock the line grid: search line-period x phase for the strongest sync comb ----
best = (-1, LINE, 0)
periods = np.arange(LINE-6, LINE+7)                 # tolerate small clock error
nlines_full = len(corr)//LINE - 1
for P in periods:
    nl = len(corr)//P - 1
    if nl < 10: continue
    starts = (np.arange(nl)*P)
    # for each phase offset, sample the comb; vectorize over a coarse phase grid
    for ph in range(0, P, 2):
        idx = starts + ph
        idx = idx[idx < len(corr)]
        score = corr[idx].mean()
        if score > best[0]:
            best = (score, P, ph)
lock_score, P, ph = best
# quality = locked-comb mean / global mean correlation (how much the sync stands out)
gmean = corr.mean() + 1e-9
SYNC_LOCK = lock_score / gmean

# ---- assemble image on the locked grid ----
nl = (len(px) - ph)//P - 1
rows = []
for L in range(nl):
    s = ph + L*P
    line = px[s:s+LINE]
    if len(line) == LINE: rows.append(line)
img = np.array(rows)
if img.size == 0:
    print(f"SYNC_LOCK {SYNC_LOCK:.2f} | no lines assembled"); sys.exit(1)

# channel slices (standard APT layout)
A = img[:, 86:995]; B = img[:, 1126:2035]
sel = {'A':A,'B':B,'AB':np.hstack([A,B])}.get(CHAN, A)

# robust normalize (2-98 percentile)
lo, hi = np.percentile(sel, 2), np.percentile(sel, 98)
norm = np.clip((sel - lo)/(hi - lo + 1e-9), 0, 1)
Image.fromarray((norm*255).astype(np.uint8), 'L').save(OUT)

verdict = "SIGNAL (image structure)" if SYNC_LOCK > 3 else ("marginal" if SYNC_LOCK > 1.8 else "NOISE (no sync)")
print(f"SYNC_LOCK {SYNC_LOCK:.2f} | period={P} phase={ph} | lines={nl} | img={sel.shape} | {verdict} | -> {OUT}")
