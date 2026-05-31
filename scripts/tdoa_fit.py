#!/usr/bin/env python3
# Compare a measured TDOA lag curve (tdoa.rail "TDOA w lag") against the known/predicted
# differential-delay curve. corr + exact-window count = the geometric-agreement proof.
import sys, numpy as np
meas={}
for l in open(sys.argv[1]):
    if l.startswith('TDOA'): p=l.split(); meas[int(p[1])]=int(p[2])
truth=np.load('/tmp/tdoa_truth.npy')
nw=min(len(truth),len(meas))
m=np.array([meas[i] for i in range(nw)]); t=truth[:nw].astype(int)
corr=float(np.corrcoef(m,t)[0,1]) if np.std(m)>0 and np.std(t)>0 else 0.0
exact=int(np.sum(m==t)); rms=float(np.sqrt(np.mean((m-t)**2)))
print(f'windows {nw}  corr {corr:.4f}  exact {exact}/{nw}  rms {rms:.2f} samples')
print('measured:', list(m)); print('truth:   ', list(t))
