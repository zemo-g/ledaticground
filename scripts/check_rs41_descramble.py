#!/usr/bin/env python3
# RS41-3 checker: the Rail DESCRAMBLED hex line must reproduce /tmp/rs41_truth.npy
# (the descrambled frame bytes) BYTE-EXACT, and SYNC_OFFSET must point at the frame
# start (== preamble_bytes). PASS = byte-exact descramble + correct sync offset.
import sys, numpy as np

truth = np.load('/tmp/rs41_truth.npy').astype(int).tolist()
meta = {}
for l in open('/tmp/rs41_meta.txt'):
    if '=' in l:
        k, v = l.strip().split('=', 1); meta[k] = v
expect_off = int(meta['preamble_bytes'])

descr = None; off = None
for l in open(sys.argv[1]):
    if l.startswith('SYNC_OFFSET'):
        off = int(l.split()[1])
    if l.startswith('DESCRAMBLED '):
        h = l.strip()[12:]
        descr = [int(h[i:i+2], 16) for i in range(0, len(h), 2)]

ok = True; msgs = []
if off == expect_off:
    msgs.append(f'sync_offset={off}')
else:
    ok = False; msgs.append(f'sync_offset={off}!={expect_off}')

if descr is None:
    ok = False; msgs.append('no DESCRAMBLED line')
else:
    n = min(len(descr), len(truth))
    nmatch = sum(1 for i in range(n) if descr[i] == truth[i])
    msgs.append(f'bytematch={nmatch}/{len(truth)}')
    if nmatch != len(truth) or len(descr) != len(truth):
        ok = False

print(f'rs41_descramble: {"PASS" if ok else "FAIL"}  ' + '  '.join(msgs))
sys.exit(0 if ok else 1)
