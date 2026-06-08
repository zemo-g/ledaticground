#!/usr/bin/env python3
# Validate src/acars_msk.rail: the printed "SYM <bits>" must match the truth bits.
# Bit sync + the head pre-key region shift/trim the recovered stream relative to truth,
# and FSK tone->bit mapping can come out inverted, so we slide the shorter stream against
# the longer over a small lag window and try both polarities, reporting the best BER.
import sys, numpy as np
truth = np.load('/tmp/acars_msk_truth.npy').astype(np.uint8)

rec = None
for l in open(sys.argv[1]):
    if l.startswith('SYM '):
        rec = np.array([int(c) for c in l.strip()[4:]], np.uint8)
if rec is None:
    print('FAIL no SYM line'); sys.exit(1)

def best_ber(a, b):
    # slide the shorter (b) across the longer (a), both polarities
    if len(a) < len(b): a, b = b, a
    best, lag, pol = 1.0, None, 0
    for L in range(0, len(a) - len(b) + 1):
        seg = a[L:L+len(b)]
        d = float(np.mean(seg != b)); di = float(np.mean(seg != (1-b)))
        if d  < best: best, lag, pol = d,  L, 0
        if di < best: best, lag, pol = di, L, 1
    return best, lag, pol

best, lag, pol = best_ber(rec, truth)
ok = best < 0.02
print(f'recovered {len(rec)} truth {len(truth)}  best BER {best:.4f} @lag {lag} '
      f'({"inverted" if pol else "direct"})  {"PASS" if ok else "FAIL"}')
sys.exit(0 if ok else 1)
