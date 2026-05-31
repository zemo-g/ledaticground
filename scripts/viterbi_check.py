#!/usr/bin/env python3
import sys, numpy as np
truth=np.load('/tmp/vit_truth.npy')
dec=None
for l in open(sys.argv[1]):
    if l.startswith('BITS '):
        dec=np.array([int(c) for c in l.strip()[5:]],dtype=np.uint8)
if dec is None: print('no BITS line'); sys.exit(1)
nw=min(len(truth),len(dec))
errs=int(np.sum(truth[:nw]!=dec[:nw]))
print(f'decoded {len(dec)} bits, compared {nw}, bit errors {errs}  BER {errs/nw:.2e}')
sys.exit(0 if errs==0 else 1)
