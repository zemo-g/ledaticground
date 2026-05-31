#!/usr/bin/env python3
# Synthetic two-station recording for validating tdoa.rail. Station A records a
# broadband signal; station B records the SAME signal with a known time-VARYING
# differential delay tau(t) (samples) — the shape a real ~300 km baseline imposes
# as a LEO satellite crosses the sky (tau ~ +/-(baseline/c), ~+/-60 samples @ 60 kHz,
# crossing 0 near equidistant approach). Light decorrelated noise on each station.
import numpy as np
fs=60000; n=8192; lmax=64; nw=20
M = nw*n + 4*lmax
rng=np.random.default_rng(11)
s = rng.standard_normal(M + 4*lmax)                 # broadband base signal
w_idx=np.arange(nw); tc=0.5*(nw-1)
tau_true=np.round(50*np.tanh((w_idx-tc)/(0.22*nw))).astype(int)   # +50..-50 samples
A=np.empty(M); B=np.empty(M)
for k in range(M): A[k]=s[k+2*lmax]
for w in range(nw):
    base=w*n+lmax; tw=int(tau_true[w])
    for i in range(n):
        k=base+i
        if k<M: B[k]=s[k+2*lmax-tw]
# margins (outside any window) — fill with zero-lag copy so reads are valid
for k in range(M):
    if B[k]==0 and (k<lmax or k>=nw*n+lmax): B[k]=s[k+2*lmax]
A += rng.standard_normal(M)*0.2
B += rng.standard_normal(M)*0.2
def to_iq(x):
    I=np.clip(127.5+40*x,0,255).astype(np.uint8)
    Q=np.full(len(x),127,np.uint8)
    iq=np.empty(2*len(x),np.uint8); iq[0::2]=I; iq[1::2]=Q; return iq
to_iq(A).tofile('/tmp/tdoa_a.iq'); to_iq(B).tofile('/tmp/tdoa_b.iq')
np.save('/tmp/tdoa_truth.npy', tau_true)
print('tau_true (samples):', list(tau_true))
