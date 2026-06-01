#!/usr/bin/env python3
import sys, numpy as np
truth=np.load('/tmp/ais_truth.npy')
rec=None
for l in open(sys.argv[1]):
    if l.startswith('BITS '): rec=np.array([int(c) for c in l.strip()[5:]],np.uint8)
if rec is None: print('no BITS'); sys.exit(1)
# GMSK discriminator has a 1-symbol differential ambiguity; try direct + inverted
nb=min(len(truth),len(rec)); t=truth[:nb]; r=rec[:nb]
e1=np.mean(r!=t); e2=np.mean((1-r)!=t)
ber=min(e1,e2)
print(f'bits {nb}  BER {ber:.4f}  ({"inverted" if e2<e1 else "direct"})')
sys.exit(0 if ber<0.02 else 1)
