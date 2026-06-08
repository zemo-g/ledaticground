#!/usr/bin/env python3
# Rung 2 checker: validate src/beacon_fsk.rail BITS vs truth (both modes).
# FSK discriminator + continuous-phase tone correlators can have a global polarity
# ambiguity, so try direct and inverted; PASS if best BER < 0.02.
import sys, numpy as np

truth = np.load('/tmp/beacon_fsk_truth.npy').astype(np.uint8)
params = np.load('/tmp/beacon_fsk_params.npy')
mode = int(params[0]); modename = 'AFSK1200' if mode==0 else 'FSK9600'

rec = None
for l in open(sys.argv[1]):
    if l.startswith('BITS '):
        rec = np.array([int(c) for c in l.strip()[5:]], np.uint8)
if rec is None:
    print(f'beacon_fsk ({modename}): FAIL  no BITS line'); sys.exit(1)

nb = min(len(truth), len(rec)); t = truth[:nb]; r = rec[:nb]
e1 = np.mean(r != t); e2 = np.mean((1-r) != t)
ber = min(e1, e2); pol = 'inverted' if e2 < e1 else 'direct'
ok = ber < 0.02
print(f'beacon_fsk ({modename}): {"PASS" if ok else "FAIL"}  bits={nb}  BER={ber:.4f}  ({pol})')
sys.exit(0 if ok else 1)
