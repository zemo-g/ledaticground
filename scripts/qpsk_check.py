#!/usr/bin/env python3
# Recovered QPSK bits have a 4-fold phase ambiguity (carrier recovery locks to any of
# 4 rotations). Try all 4 rotations of the recovered symbol stream, report best SER.
import sys, numpy as np
truth=np.load('/tmp/qpsk_truth.npy')          # 2N bits, interleaved I,Q
rec=None
for l in open(sys.argv[1]):
    if l.startswith('SYM '):
        rec=np.array([int(c) for c in l.strip()[4:]],dtype=np.uint8)
if rec is None: print('no SYM line'); sys.exit(1)
nb=min(len(truth),len(rec)); rec=rec[:nb]; tr=truth[:nb]
ti=tr[0::2]; tq=tr[1::2]; ri=rec[0::2]; rq=rec[1::2]
# the 4 rotations of a QPSK symbol expressed as (I,Q)-bit transforms
def rot0(i,q): return i,q
def rot90(i,q): return q,1-i
def rot180(i,q): return 1-i,1-q
def rot270(i,q): return 1-q,i
best=1.0;bo='?'
for name,f in [('0',rot0),('90',rot90),('180',rot180),('270',rot270)]:
    fi,fq=f(ri,rq)
    ser=np.mean((fi!=ti)|(fq!=tq))
    if ser<best: best=ser; bo=name
print(f'symbols {len(ti)}  best rotation {bo}  SER {best:.4f}')
sys.exit(0 if best<0.05 else 1)
