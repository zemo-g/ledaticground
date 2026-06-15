#!/usr/bin/env python3
# RS41-2 checker: diff the Rail BITS line against the planted channel bits
# (/tmp/rs41_bits.npy), allowing a small leading offset (the integrate-and-dump
# window may start a fraction of a symbol early/late vs the planted stream).
# PASS = best-alignment bit match >= 99% over the overlapped region.
import sys, numpy as np

truth = np.load('/tmp/rs41_bits.npy').astype(int).tolist()
bits = None
for l in open(sys.argv[1]):
    if l.startswith('BITS '):
        bits = [int(c) for c in l.strip()[5:] if c in '01']
if bits is None:
    print('rs41_demod: FAIL no BITS line'); sys.exit(1)

best = (0.0, 0)
for off in range(-3, 4):
    # align truth[i] with bits[i+off]
    n = 0; m = 0
    for i in range(len(truth)):
        j = i + off
        if 0 <= j < len(bits):
            n += 1
            if truth[i] == bits[j]:
                m += 1
    if n > 0:
        frac = m/n
        if frac > best[0]:
            best = (frac, off)

frac, off = best
ok = frac >= 0.99
print(f'rs41_demod: {"PASS" if ok else "FAIL"}  match={frac*100:.2f}% offset={off} '
      f'truth_bits={len(truth)} recovered_bits={len(bits)}')
sys.exit(0 if ok else 1)
