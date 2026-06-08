#!/usr/bin/env python3
# Validate src/gardner.rail decisions vs truth bits, allowing the QPSK 4-fold rotation
# and a small symbol lag (the loop has start-up transient + integer-strobe phase).
import sys, numpy as np
truth = np.load('/tmp/gardner_truth.npy')   # 2N interleaved I,Q bits
rec = None
for l in open(sys.argv[1]):
    if l.startswith('SYM '):
        rec = np.array([int(c) for c in l.strip()[4:]], dtype=np.uint8)
if rec is None:
    print('no SYM line'); sys.exit(1)

ti_all = truth[0::2]; tq_all = truth[1::2]
ri_all = rec[0::2];   rq_all = rec[1::2]

def rot(i,q,name):
    if name=='0':   return i,q
    if name=='90':  return q,1-i
    if name=='180': return 1-i,1-q
    if name=='270': return 1-q,i

best = 1.0; binfo=('?',0)
# allow a symbol lag of 0..4 (filter group delay / strobe alignment)
for lag in range(0,5):
    n = min(len(ti_all)-lag, len(ri_all))
    if n <= 10: continue
    ti = ti_all[lag:lag+n]; tq = tq_all[lag:lag+n]
    ri = ri_all[:n];        rq = rq_all[:n]
    for name in ['0','90','180','270']:
        fi,fq = rot(ri,rq,name)
        # skip transient: measure over the back 80%
        s = n//5
        ser = np.mean((fi[s:]!=ti[s:]) | (fq[s:]!=tq[s:]))
        if ser < best:
            best = ser; binfo=(name,lag)
print(f"symbols {len(ri_all)}  best rotation {binfo[0]} lag {binfo[1]}  SER {best:.4f}")
sys.exit(0 if best < 0.05 else 1)
